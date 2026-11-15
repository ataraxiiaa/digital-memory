"""Extraction provenance and citation validation, without running extraction."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import pypdf
from pydantic import ValidationError
from pypdf import PdfReader

from backend.schemas.extraction import BoundingBox, CitationReference, ExtractionRun


@pytest.fixture
def record():
    return {
        "id": str(uuid4()), "document_id": str(uuid4()),
        "source_sha256": "a" * 64, "created_at": "2026-11-15T10:00:00Z",
        "tool_versions": {"contract-test": "1"}, "page_count": 1,
        "status": "completed", "pages": [{
            "page_number": 1, "width_points": 612, "height_points": 792,
            "status": "processed", "regions": [{
                "id": str(uuid4()), "kind": "text", "status": "extracted",
                "method": "native", "text": "Budget\n\nCafé 🙂 approved.",
            }],
        }],
    }


def test_unicode_citation_survives_serialization(record):
    run = ExtractionRun.model_validate_json(ExtractionRun.model_validate(record).model_dump_json())
    citation = CitationReference(
        document_id=run.document_id, extraction_id=run.id, page_number=1,
        region_id=run.pages[0].regions[0].id, start_char=8, end_char=14,
    )
    assert run.resolve_citation(citation) == "Café 🙂"
    with pytest.raises(ValueError, match="beyond"):
        run.resolve_citation(citation.model_copy(update={"end_char": 1000}))
    with pytest.raises(ValueError, match="different"):
        run.resolve_citation(citation.model_copy(update={"extraction_id": uuid4()}))
    with pytest.raises(ValueError, match="does not exist"):
        run.resolve_citation(citation.model_copy(update={"region_id": uuid4()}))


@pytest.mark.parametrize("issue", ["missing_page", "duplicate_page", "duplicate_region", "pending", "review", "timezone"])
def test_rejects_inconsistent_completion(record, issue):
    if issue == "missing_page":
        record["page_count"] = 2
    elif issue == "duplicate_page":
        record["pages"].append(deepcopy(record["pages"][0]))
    elif issue == "duplicate_region":
        record["pages"][0]["regions"] *= 2
    elif issue == "pending":
        record["pages"][0]["status"] = "pending"
    elif issue == "review":
        record["pages"][0]["regions"][0]["status"] = "needs_review"
    else:
        record["created_at"] = "2026-11-15T10:00:00"
    with pytest.raises(ValidationError):
        ExtractionRun.model_validate(record)


def test_partial_results_and_review_are_explicit(record):
    record["status"] = "processing"
    record["page_count"] = 2
    assert len(ExtractionRun.model_validate(record).pages) == 1
    record["page_count"] = 1
    record["status"] = "completed_with_warnings"
    record["pages"][0]["status"] = "needs_review"
    record["pages"][0]["warnings"] = ["No text extracted; inspect the original page"]
    assert ExtractionRun.model_validate(record).pages[0].warnings


@pytest.mark.parametrize("box", [
    {"x0": 0.8, "y0": 0, "x1": 0.2, "y1": 1},
    {"x0": -1, "y0": 0, "x1": 1, "y1": 1},
    {"x0": 0, "y0": 0, "x1": float("nan"), "y1": 1},
])
def test_rejects_invalid_coordinates(box):
    with pytest.raises(ValidationError):
        BoundingBox.model_validate(box)


def test_attention_fixture_page_inventory():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "attention.pdf"
    if not path.is_file():
        pytest.skip("Add tests/fixtures/attention.pdf to run the local paper check")
    reader = PdfReader(path)
    assert not reader.is_encrypted
    run = ExtractionRun(
        id=uuid4(), document_id=uuid4(), source_sha256=sha256(path.read_bytes()).hexdigest(),
        created_at="2026-11-15T10:00:00Z", tool_versions={"pypdf": pypdf.__version__},
        page_count=len(reader.pages), status="queued", pages=[{
            "page_number": number, "width_points": float(page.mediabox.width),
            "height_points": float(page.mediabox.height), "status": "pending",
        } for number, page in enumerate(reader.pages, start=1)],
    )
    assert len(run.pages) == run.page_count
    assert run.pages[-1].page_number == run.page_count
