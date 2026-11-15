"""OCR provenance tests and an explicitly enabled local model integration check."""

from io import BytesIO
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.schemas.extraction import ExtractionRun, CitationReference
from backend.schemas.layout import LayoutReport
from backend.services.pdf_ocr import map_ocr_extraction


def native_run(text="Native source"):
    return ExtractionRun(id=uuid4(), document_id=uuid4(), source_sha256="a" * 64,
        created_at="2026-11-15T10:00:00Z", tool_versions={"test": "1"},
        page_count=1, status="completed", pages=[{
            "page_number": 1, "width_points": 100, "height_points": 200,
            "status": "processed", "regions": [{"id": uuid4(), "kind": "text",
                "status": "extracted", "method": "native", "text": text}],
        }])


def line(text="Café 2000", confidence=0.95, from_ocr=True, left=10):
    box = SimpleNamespace(to_top_left_origin=lambda page_height:
        SimpleNamespace(l=left, t=20, r=90, b=40))
    return SimpleNamespace(text="normalized text", orig=text, confidence=confidence,
        from_ocr=from_ocr, rect=SimpleNamespace(to_bounding_box=lambda: box))


def detected(lines):
    return SimpleNamespace(page_no=1, size=SimpleNamespace(width=100, height=200),
        parsed_page=SimpleNamespace(textline_cells=lines))


def test_ocr_is_separate_exactly_citable_and_does_not_duplicate_native():
    native = native_run()
    before = native.model_dump_json()
    ocr = map_ocr_extraction(native, [detected([line(from_ocr=False), line()])], {"test": "1"})
    assert native.model_dump_json() == before
    assert ocr.id != native.id
    assert ocr.source_sha256 == native.source_sha256
    assert len(ocr.pages[0].regions) == 1
    region = ocr.pages[0].regions[0]
    assert region.text == "Café 2000"
    assert region.method == "ocr"
    assert region.confidence == 0.95
    assert region.bbox.x0 == 0.1
    assert ocr.status == "completed_with_warnings"
    ref = CitationReference(document_id=ocr.document_id, extraction_id=ocr.id,
        page_number=1, region_id=region.id, start_char=5, end_char=9)
    assert ocr.resolve_citation(ref) == "2000"
    with pytest.raises(ValueError, match="different"):
        native.resolve_citation(ref)


def test_no_ocr_additions_on_native_page():
    ocr = map_ocr_extraction(native_run(), [detected([line(from_ocr=False)])], {"test": "1"})
    assert ocr.status == "completed"
    assert not ocr.pages[0].regions


def test_no_text_on_blank_or_unreadable_page_requires_review():
    ocr = map_ocr_extraction(native_run(""), [detected([])], {"test": "1"})
    assert ocr.pages[0].status == "needs_review"
    assert "blank, graphical, or unreadable" in ocr.pages[0].warnings[0]


def test_missing_ocr_page_is_failed():
    ocr = map_ocr_extraction(native_run(), [], {"test": "1"})
    assert ocr.status == "failed"
    assert ocr.pages[0].status == "failed"


def test_low_confidence_and_invalid_lines_are_reported():
    ocr = map_ocr_extraction(native_run(),
        [detected([line(confidence=0.7), line(left=95)])], {"test": "1"})
    assert len(ocr.pages[0].regions) == 1
    assert any("0.8" in note for note in ocr.pages[0].regions[0].warnings)
    assert any("omitted" in note for note in ocr.pages[0].warnings)


def test_report_rejects_ocr_from_other_document():
    native = native_run()
    ocr = map_ocr_extraction(native_run(), [detected([line()])], {"test": "1"})
    with pytest.raises(ValidationError, match="same source"):
        LayoutReport(native_extraction=native, detector="test", model_repository="test",
            model_revision="test", tool_versions={}, options={},
            pages=[{"page_number": 1, "status": "detected"}], ocr_extraction=ocr)


def test_suspect_characters_are_preserved_with_warning():
    ocr = map_ocr_extraction(native_run(), [detected([line(text="Budget \ufffd")])], {"test": "1"})
    region = ocr.pages[0].regions[0]
    assert region.text == "Budget \ufffd"
    assert any("suspect" in warning for warning in region.warnings)


def test_symbols_only_result_is_flagged_without_silent_removal():
    ocr = map_ocr_extraction(native_run(), [detected([line(text="__")])], {"test": "1"})
    region = ocr.pages[0].regions[0]
    assert region.text == "__"
    assert any("only symbols" in warning for warning in region.warnings)


def test_report_requires_citable_ocr_geometry():
    native = native_run()
    ocr = map_ocr_extraction(native, [detected([line()])], {"test": "1"})
    page = ocr.pages[0].model_copy(update={"regions": (ocr.pages[0].regions[0].model_copy(update={"bbox": None}),)})
    with pytest.raises(ValidationError, match="coordinates"):
        LayoutReport(native_extraction=native, detector="test", model_repository="test",
            model_revision="test", tool_versions={}, options={},
            pages=[{"page_number": 1, "status": "detected"}],
            ocr_extraction=ocr.model_copy(update={"pages": (page,)}))


def make_ocr_sample():
    """Native, image-only, mixed-content, and blank pages, all synthetic."""
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfWriter, PdfReader, Transformation
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

    image = Image.new("RGB", (1200, 1500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    for y, text in [(150, "Project budget review"), (240, "Approved equipment budget: 2000 USD"),
                    (330, "Decision date: 15 November 2026")]:
        draw.text((90, y), text, fill="black", font=font)
    scanned = BytesIO()
    image.save(scanned, format="PDF", resolution=150)
    scanned.seek(0)
    reader = PdfReader(scanned)
    writer = PdfWriter()

    def add_native():
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 50 700 Td (Native project note. Budget approved.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        return page

    add_native()
    writer.add_page(reader.pages[0])
    mixed = add_native()
    mixed.merge_transformed_page(reader.pages[0], Transformation().scale(.6).translate(50, 100))
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.skipif(os.environ.get("DIGITAL_MEMORY_RUN_OCR_TESTS") != "1",
    reason="Set DIGITAL_MEMORY_RUN_OCR_TESTS=1 for local model inference and possible model downloads")
def test_local_ocr_recovers_scan_and_mixed_content_without_replacing_native():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("docling")
    from backend.services.pdf_layout import detect_pdf_layout

    report = detect_pdf_layout(make_ocr_sample(), document_id=uuid4(), include_ocr=True)
    native = report.native_extraction
    ocr = report.ocr_extraction
    assert native.pages[0].regions[0].text.strip() == "Native project note. Budget approved."
    assert not native.pages[1].regions[0].text.strip()
    assert not ocr.pages[0].regions
    for number in [2, 3]:
        text = "\n".join(region.text for region in ocr.pages[number - 1].regions)
        assert "2000" in text
        assert "2026" in text
        assert "Native project note" not in text
    assert ocr.pages[3].status == "needs_review"
    assert not ocr.pages[3].regions
