"""Layout adapter checks without model downloads or optional ML dependencies."""

from types import SimpleNamespace
from uuid import uuid4
from io import BytesIO

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from backend.schemas.extraction import ExtractionRun, CitationReference
from backend.services.pdf_layout import map_layout_pages
from backend.services.pdf_layout import render_preview
from backend.schemas.layout import LayoutReport


def native_run():
    return ExtractionRun(id=uuid4(), document_id=uuid4(), source_sha256="a" * 64,
        created_at="2026-11-15T10:00:00Z", tool_versions={"test": "1"},
        page_count=1, status="completed", pages=[{
            "page_number": 1, "width_points": 1000, "height_points": 2000,
            "status": "processed", "regions": [{"id": uuid4(), "kind": "text",
                "status": "extracted", "method": "native", "text": "Original text"}],
        }])


def detected_page(label="table", confidence=0.9, origin="top", left=100, right=600):
    def top_left(page_height):
        if origin == "bottom":
            return SimpleNamespace(l=left, t=page_height - 1800, r=right, b=page_height - 1000)
        return SimpleNamespace(l=left, t=200, r=right, b=1000)
    cluster = SimpleNamespace(label=SimpleNamespace(value=label), confidence=confidence,
        bbox=SimpleNamespace(to_top_left_origin=top_left))
    return SimpleNamespace(page_no=1, size=SimpleNamespace(width=1000, height=2000),
        predictions=SimpleNamespace(layout=SimpleNamespace(clusters=[cluster])))


def test_maps_page_number_coordinates_and_warns_for_tables():
    native = native_run()
    before = native.model_dump_json()
    result = map_layout_pages(native, [detected_page()])[0]
    assert result.page_number == 1
    assert result.status == "needs_review"
    assert result.regions[0].kind == "table"
    assert result.regions[0].bbox.model_dump() == {"x0": 0.1, "y0": 0.1, "x1": 0.6, "y1": 0.5}
    assert result.regions[0].confidence == 0.9
    assert native.model_dump_json() == before
    reference = CitationReference(document_id=native.document_id, extraction_id=native.id,
        page_number=1, region_id=native.pages[0].regions[0].id, start_char=0, end_char=8)
    assert native.resolve_citation(reference) == "Original"


def test_bottom_origin_conversion_and_clipping():
    page = map_layout_pages(native_run(), [detected_page(origin="bottom", left=-1, right=1001)])[0]
    assert page.regions[0].bbox.model_dump() == {"x0": 0.0, "y0": 0.1, "x1": 1.0, "y1": 0.5}


def test_missing_page_is_explicit_failure():
    page = map_layout_pages(native_run(), [])[0]
    assert page.status == "failed"
    assert page.warnings


def test_unknown_label_and_low_confidence_are_reviewable():
    page = map_layout_pages(native_run(), [detected_page("unrecognized", 0.4)])[0]
    assert page.regions[0].kind == "unknown"
    assert page.regions[0].source_label == "unrecognized"
    assert len(page.warnings) == 2


def test_degenerate_region_does_not_erase_page_record():
    page = map_layout_pages(native_run(), [detected_page(left=100, right=50)])[0]
    assert page.status == "needs_review"
    assert not page.regions


def test_text_only_page_is_detected_without_claiming_accuracy():
    page = map_layout_pages(native_run(), [detected_page("text")])[0]
    assert page.status == "detected"
    assert page.regions[0].kind == "text"


def report_for(native, pages):
    return LayoutReport(native_extraction=native, detector="test", model_repository="test",
        model_revision="test", tool_versions={"test": "1"}, options={}, pages=pages)


def test_report_rejects_missing_and_duplicate_pages():
    native = native_run()
    pages = map_layout_pages(native, [detected_page()])
    for invalid in [(), pages + pages]:
        with pytest.raises(ValidationError, match="every physical page"):
            report_for(native, invalid)


def test_html_preview_embeds_page_and_escapes_model_labels():
    pytest.importorskip("pypdfium2")
    writer = PdfWriter()
    writer.add_blank_page(width=1000, height=2000)
    data = BytesIO()
    writer.write(data)
    native = native_run()
    pages = map_layout_pages(native, [detected_page("<script>bad</script>")])
    html = render_preview(data.getvalue(), report_for(native, pages))
    assert "data:image/png;base64," in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "<script>bad</script>" not in html
    assert "left:10.0%" in html
