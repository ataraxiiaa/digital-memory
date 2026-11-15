"""Native PDF extraction checked against actual in-memory PDF content streams."""

from hashlib import sha256
from io import BytesIO
from uuid import uuid4
import sys

import pytest
from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.schemas.extraction import CitationReference
from backend.schemas.extraction import ExtractionRun
from backend.services import pdf_extraction
from backend.services.pdf_extraction import PDFExtractionError, extract_pdf_text


def make_pdf(texts, *, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        if text:
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("test-only")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extracts_every_page_and_resolves_exact_citation():
    data = make_pdf(["First page budget", "Middle decision", "Final approval"])
    document_id = uuid4()
    run = extract_pdf_text(data, document_id=document_id)
    assert run.status == "completed"
    assert run.source_sha256 == sha256(data).hexdigest()
    assert run.document_id == document_id
    assert [page.page_number for page in run.pages] == [1, 2, 3]
    assert [page.regions[0].text for page in run.pages] == [
        "First page budget", "Middle decision", "Final approval"
    ]
    page = run.pages[-1]
    citation = CitationReference(document_id=document_id, extraction_id=run.id,
        page_number=3, region_id=page.regions[0].id, start_char=6, end_char=14)
    assert run.resolve_citation(citation) == "approval"
    assert all(page.width_points == 612 for page in run.pages)
    assert run.tool_versions["pypdf"]


def test_empty_page_keeps_its_number_and_requires_review():
    run = extract_pdf_text(make_pdf(["Before", "", "After"]), document_id=uuid4())
    assert run.status == "completed_with_warnings"
    assert run.pages[1].status == "needs_review"
    assert run.pages[1].regions[0].text == ""
    assert run.pages[1].warnings
    assert run.pages[2].regions[0].text == "After"


def test_page_failure_preserves_other_pages_and_hides_error_details(monkeypatch):
    original = PageObject.extract_text

    def extract(page, *args, **kwargs):
        if b"Broken" in page.get_contents().get_data():
            raise RuntimeError("sensitive parser internals")
        return original(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", extract)
    run = extract_pdf_text(make_pdf(["Before", "Broken", "After"]), document_id=uuid4())
    assert run.status == "completed_with_warnings"
    assert [page.status for page in run.pages] == ["processed", "failed", "processed"]
    assert "sensitive" not in run.model_dump_json()


@pytest.mark.parametrize("data", [b"not a PDF", b"%PDF-broken", make_pdf([]), make_pdf(["Secret"], encrypted=True)])
def test_invalid_documents_are_rejected(data):
    with pytest.raises(PDFExtractionError):
        extract_pdf_text(data, document_id=uuid4())


def test_cli_writes_valid_json_and_refuses_to_overwrite(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    target = tmp_path / "result.json"
    source.write_bytes(make_pdf(["Approved budget"]))
    document_id = uuid4()
    monkeypatch.setattr(sys, "argv", ["pdf_extraction", str(source),
        "--document-id", str(document_id), "--output", str(target)])
    pdf_extraction.main()
    saved = target.read_text(encoding="utf-8")
    run = ExtractionRun.model_validate_json(saved)
    assert run.document_id == document_id
    assert run.pages[0].regions[0].text == "Approved budget"
    with pytest.raises(SystemExit) as error:
        pdf_extraction.main()
    assert error.value.code == 1
    assert target.read_text(encoding="utf-8") == saved


def test_all_page_failures_mark_run_failed(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("parser failure")
    monkeypatch.setattr(PageObject, "extract_text", fail)
    run = extract_pdf_text(make_pdf(["One", "Two"]), document_id=uuid4())
    assert run.status == "failed"
    assert len(run.pages) == 2
