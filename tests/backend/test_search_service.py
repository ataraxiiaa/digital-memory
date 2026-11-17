"""Search payloads and snippet construction: exact citations and no markup."""

from uuid import UUID

from backend.services.search import (
    START, STOP, highlight_words, passage_rows, query_words, snippet_segments,
)
from test_source_passages import add_ocr, report_for


def test_passage_rows_carry_exact_citations_that_resolve_to_their_text():
    report = add_ocr(report_for(["Budget approved. Travel is covered.", "Second page text."]), "Scanned note")
    rows, skipped = passage_rows(report)
    runs = {report.native_extraction.id: report.native_extraction, report.ocr_extraction.id: report.ocr_extraction}
    assert len(rows) >= 4 and skipped == 0
    for row in rows:
        citation = row["citation"]
        assert UUID(citation["document_id"]) == report.native_extraction.document_id
        assert runs[UUID(citation["extraction_id"])].resolve_citation(
            __import__("backend.schemas.extraction", fromlist=["CitationReference"]).CitationReference.model_validate(citation)) == row["text"]
        assert row["end_char"] - row["start_char"] == len(row["text"])
        assert (row["page_number"], row["region_id"], row["start_char"], row["end_char"]) == (
            citation["page_number"], citation["region_id"], citation["start_char"], citation["end_char"])
    assert {row["method"] for row in rows} == {"native", "ocr"}
    assert any(row["review_required"] for row in rows if row["method"] == "ocr")


def test_snippet_segments_mark_matches_and_drop_stray_markers():
    marked = f"We approved the {START}budget{STOP} for {START}Marigold{STOP} today"
    assert [(s.text, s.match) for s in snippet_segments(marked)] == [
        ("We approved the ", False), ("budget", True), (" for ", False), ("Marigold", True), (" today", False)]
    # An unclosed marker never leaks into text, and an extra close does not flip state.
    assert [(s.text, s.match) for s in snippet_segments(f"a {START}b")] == [("a ", False), ("b", True)]
    assert [(s.text, s.match) for s in snippet_segments(f"a{STOP}{STOP} b")] == [("a", False), (" b", False)]
    assert snippet_segments(None) == [] and snippet_segments("") == []


def test_markup_in_source_text_stays_inert_data():
    segments = snippet_segments(f"<script>alert(1)</script> {START}budget{STOP} & <b>")
    assert "".join(s.text for s in segments) == "<script>alert(1)</script> budget & <b>"
    assert [s.text for s in segments if s.match] == ["budget"]


def test_highlight_words_marks_every_case_insensitive_occurrence():
    segments = highlight_words("Larry Page and L. Page", ["page"])
    assert [(s.text, s.match) for s in segments] == [
        ("Larry ", False), ("Page", True), (" and L. ", False), ("Page", True)]
    assert "".join(s.text for s in highlight_words("Café résumé", ["caf"])) == "Café résumé"
    assert [s.match for s in highlight_words("abc", [""])] == [False]
    assert highlight_words("", ["x"]) == []


def test_highlight_words_does_not_misalign_when_case_folding_changes_length():
    # "İ" lowercases to two code points; highlighting is skipped rather than misplaced.
    segments = highlight_words("İstanbul trip", ["trip"])
    assert "".join(s.text for s in segments) == "İstanbul trip"


def test_query_words_split_on_any_whitespace_and_lowercase():
    assert query_words("  L.  Page\tLARRY\n") == ["l.", "page", "larry"]
    assert query_words("   ") == []
