"""Passage boundaries, stable identities, and exact evidence references."""

import re
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.schemas.extraction import ExtractionRun
from backend.schemas.layout import LayoutReport
from backend.schemas.passages import SourcePassage
from backend.services.source_passages import build_source_passages, passage_page, passage_spans


def report_for(texts):
    native = ExtractionRun(id=uuid4(), document_id=uuid4(), source_sha256="a" * 64,
        created_at="2026-11-15T10:00:00Z", tool_versions={"test": "1"},
        page_count=len(texts), status="completed", pages=[{
            "page_number": number, "width_points": 612, "height_points": 792,
            "status": "processed", "regions": [{"id": uuid4(), "kind": "text",
                "method": "native", "status": "extracted", "text": text}],
        } for number, text in enumerate(texts, 1)])
    return LayoutReport(native_extraction=native, detector="test", model_repository="test",
        model_revision="1", tool_versions={"test": "1"}, options={}, pages=[{
            "page_number": number, "status": "detected",
        } for number in range(1, len(texts) + 1)])


def add_ocr(report, text="Scanned budget: 2000 USD"):
    native = report.native_extraction
    ocr = ExtractionRun(id=uuid4(), document_id=native.document_id, source_sha256=native.source_sha256,
        created_at="2026-11-15T10:00:00Z", tool_versions={"ocr-test": "1"},
        page_count=native.page_count, status="completed_with_warnings", pages=[{
            "page_number": page.page_number, "width_points": 612, "height_points": 792,
            "status": "needs_review", "warnings": ["OCR requires review"], "regions": [{
                "id": uuid4(), "kind": "text", "method": "ocr", "status": "needs_review",
                "text": text, "confidence": .75, "bbox": {"x0": .1, "y0": .1, "x1": .9, "y1": .2},
            }],
        } for page in native.pages])
    return report.model_copy(update={"ocr_extraction": ocr})


@pytest.mark.parametrize("text,max_chars", [
    ("First paragraph.\r\n\r\nSara approved the budget.\n" * 70 + "Final decision 🌍", 64),
    ("αβγ🙂" * 80, 31),
    ("  \n\t" * 50, 7),
    ("A", 1),
])
def test_spans_cover_all_non_whitespace_without_overlap(text, max_chars):
    spans = list(passage_spans(text, max_chars))
    previous = 0
    for start, end in spans:
        assert previous <= start < end <= len(text)
        assert end - start <= max_chars
        assert not text[previous:start].strip()
        assert text[start:end].strip()
        previous = end
    assert not text[previous:].strip()
    assert re.sub(r"\s", "", "".join(text[a:b] for a, b in spans)) == re.sub(r"\s", "", text)


def test_prefers_paragraph_then_sentence_boundaries():
    text = "First paragraph.\n\nSecond paragraph is much longer than our window."
    assert next(passage_spans(text, 30)) == (0, len("First paragraph.\n\n"))
    text = "Sara approved it. Mark recorded the amount in the notes."
    assert next(passage_spans(text, 30)) == (0, len("Sara approved it. "))


def test_every_passage_resolves_to_unchanged_source_on_its_own_page():
    report = report_for(["  Café 🙂 approved.\n\n" * 60, "Last page: end of source."])
    passages, _ = build_source_passages(report, max_chars=80)
    for index, passage in enumerate(passages, 1):
        assert passage.ordinal == index
        assert passage.text == report.native_extraction.resolve_citation(passage.citation)
        assert len(passage.text) <= 80
        assert passage.source_sha256 == report.native_extraction.source_sha256
    assert passages[-1].citation.page_number == 2
    assert passages[-1].text == "Last page: end of source."


def test_stable_ids_survive_serialization_and_change_with_source_version_or_options():
    report = report_for(["A budget decision. " * 30])
    first, _ = build_source_passages(report, max_chars=64)
    copied = LayoutReport.model_validate_json(report.model_dump_json())
    second, _ = build_source_passages(copied, max_chars=64)
    assert first == second
    assert len({passage.id for passage in first}) == len(first)
    changed = report.model_copy(update={"native_extraction": report.native_extraction.model_copy(update={"id": uuid4()})})
    assert build_source_passages(changed, max_chars=64)[0][0].id != first[0].id
    assert build_source_passages(report, max_chars=80)[0][0].id != first[0].id


def test_ocr_keeps_separate_provenance_and_review_score():
    report = add_ocr(report_for(["Original budget note", "Second page"]))
    passages, _ = build_source_passages(report)
    assert [passage.method for passage in passages] == ["native", "ocr", "native", "ocr"]
    for passage in passages:
        run = report.native_extraction if passage.method == "native" else report.ocr_extraction
        assert run.resolve_citation(passage.citation) == passage.text
        if passage.method == "ocr":
            assert passage.review_required
            assert passage.confidence == .75
            assert "OCR requires review" in passage.warnings


def test_layout_and_table_warnings_are_inherited_without_changing_text():
    report = report_for(["Budget table flattened in the original native text."])
    data = report.model_dump()
    data["pages"][0].update(status="needs_review", warnings=["Layout requires review"])
    data["tables"] = [{"id": uuid4(), "source_ref": "#/tables/0", "page_number": 1,
        "bbox": {"x0": .1, "y0": .1, "x1": .9, "y1": .5}, "num_rows": 1, "num_cols": 1,
        "cells": [], "warnings": ["Check table values"]}]
    passages, _ = build_source_passages(LayoutReport.model_validate(data))
    assert passages[0].review_required
    assert passages[0].warnings == ("Layout requires review", "Check table values")
    assert passages[0].text == data["native_extraction"]["pages"][0]["regions"][0]["text"]


def test_failed_and_whitespace_regions_are_excluded():
    report = report_for(["Failed text", " \n\t"])
    data = report.model_dump()
    data["native_extraction"]["status"] = "completed_with_warnings"
    data["native_extraction"]["pages"][0]["regions"][0]["status"] = "failed"
    passages, skipped = build_source_passages(LayoutReport.model_validate(data))
    assert not passages
    assert skipped == 2


def test_pagination_and_invalid_options():
    report = report_for(["Full source " * 500])
    first = passage_page(report, limit=2)
    following = passage_page(report, limit=2, offset=2)
    assert first.total > 2
    assert following.passages[0].ordinal == 3
    assert not set(x.id for x in first.passages) & set(x.id for x in following.passages)
    assert passage_page(report, offset=first.total + 1).passages == ()
    with pytest.raises(ValueError):
        build_source_passages(report, max_chars=0)
    with pytest.raises(ValueError):
        passage_page(report, offset=-1)


def test_pending_runs_are_not_used_for_entities():
    report = report_for(["Pending text"])
    pending = report.native_extraction.model_copy(update={"status": "processing"})
    with pytest.raises(ValueError, match="finalized"):
        build_source_passages(report.model_copy(update={"native_extraction": pending}))


def test_passage_model_rejects_changed_text_length():
    passage = build_source_passages(report_for(["Evidence"]))[0][0]
    data = passage.model_dump()
    data["text"] = "Different evidence"
    with pytest.raises(ValidationError, match="exact source range"):
        SourcePassage.model_validate(data)


def test_short_regions_are_not_joined_or_given_shared_offsets():
    report = report_for(["First region."])
    data = report.model_dump()
    region = dict(data["native_extraction"]["pages"][0]["regions"][0])
    region.update(id=uuid4(), text="Second region.")
    data["native_extraction"]["pages"][0]["regions"] += (region,)
    report = LayoutReport.model_validate(data)
    passages, _ = build_source_passages(report)
    assert [passage.text for passage in passages] == ["First region.", "Second region."]
    assert all(passage.citation.start_char == 0 for passage in passages)
    assert passages[0].citation.region_id != passages[1].citation.region_id
