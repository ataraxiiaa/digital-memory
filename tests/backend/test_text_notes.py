"""Exact-text adapter: offsets must resolve against the original note, unchanged."""

from uuid import uuid4

import pytest

from backend.schemas.documents import MAX_NOTE_CHARS, NewDocument
from backend.schemas.layout import LayoutReport
from backend.services.entity_mentions import FULL_LABELS, extract_entity_mentions
from backend.services.source_passages import build_source_passages
from backend.services.text_notes import NoteError, build_note_report, note_sha256
from test_entity_mentions import FakeModel, candidate

DOC = uuid4()


def region_text(report):
    return report.native_extraction.pages[0].regions[0].text


@pytest.mark.parametrize("text", [
    "Sara met Brian on 3 March 2024.",
    "Line one\r\nLine two\r\n\r\nParagraph two with trailing spaces   \r\n",
    "Café Aylanto, naïve 日本語 😀 emoji, and Łukasz.",
    "\n\n  leading blank lines and tabs\t\t",
])
def test_region_is_the_exact_note_and_citations_resolve(text):
    report = build_note_report(text, document_id=DOC)
    assert region_text(report) == text
    assert report.native_extraction.source_sha256 == note_sha256(text)
    assert report.native_extraction.status == "completed"
    passages, skipped = build_source_passages(report)
    assert skipped == 0
    # Passages partition the note: every citation slices the original text exactly.
    assert "".join(p.text for p in passages) == text
    for passage in passages:
        c = passage.citation
        assert text[c.start_char:c.end_char] == passage.text
        assert report.native_extraction.resolve_citation(c) == passage.text
        assert not passage.review_required


def test_ids_are_stable_for_identical_text_and_change_with_any_edit():
    first = build_note_report("Sara met Brian.", document_id=DOC)
    again = build_note_report("Sara met Brian.", document_id=DOC)
    edited = build_note_report("Sara met Brian!", document_id=DOC)
    other_doc = build_note_report("Sara met Brian.", document_id=uuid4())
    region = lambda r: r.native_extraction.pages[0].regions[0].id
    assert region(first) == region(again) and first.native_extraction.id == again.native_extraction.id
    assert region(first) != region(edited) != region(other_doc)
    assert first.native_extraction.source_sha256 != edited.native_extraction.source_sha256


def test_long_notes_split_into_ordered_passages_without_losing_text():
    text = "\n\n".join(f"Paragraph {i}. " + "Word " * 80 for i in range(400))
    assert len(text) < MAX_NOTE_CHARS
    passages, _ = build_source_passages(build_note_report(text, document_id=DOC))
    assert len(passages) > 100
    assert "".join(p.text for p in passages) == text
    assert [p.citation.start_char for p in passages] == sorted(p.citation.start_char for p in passages)


@pytest.mark.parametrize("text,message", [
    ("", "contain text"), ("   \n\t ", "contain text"), ("a\x00b", "null"),
    ("x" * (MAX_NOTE_CHARS + 1), "exceed"), ("lone \ud800 surrogate", "valid Unicode"),
])
def test_unusable_notes_are_rejected(text, message):
    with pytest.raises(NoteError, match=message):
        build_note_report(text, document_id=DOC)


def test_note_at_the_size_limit_is_accepted():
    assert region_text(build_note_report("a" * MAX_NOTE_CHARS, document_id=DOC)) == "a" * MAX_NOTE_CHARS


def test_creation_schema_enforces_the_same_limits():
    with pytest.raises(ValueError):
        NewDocument(title="T", content="x" * (MAX_NOTE_CHARS + 1))
    with pytest.raises(ValueError):
        NewDocument(title="T", content="bad \ud800")
    assert NewDocument(title="T", content="  keep\r\nspacing  ").content == "  keep\r\nspacing  "


def test_report_round_trips_through_the_stored_json_contract():
    report = build_note_report("Zoë\r\nlives in Lahore.", document_id=DOC)
    assert LayoutReport.model_validate(report.model_dump(mode="json")) == report


def test_entities_extract_with_exact_citations_from_a_note():
    text = "Sara approved Project Atlas on 3 March 2024.\r\n\r\nSecond paragraph in Lahore."
    report = build_note_report(text, document_id=DOC)

    def at(value):
        start = text.index(value)
        return candidate(value, start, start + len(value), .9)
    model = FakeModel({"entities": {"person": [at("Sara")], "date": [at("3 March 2024")], "location": [at("Lahore")]}})
    result = extract_entity_mentions(report, model, labels=FULL_LABELS)
    mentions = [m for p in result.passages for m in p.mentions]
    assert {m.text for m in mentions} >= {"Sara"}
    for mention in mentions:
        c = mention.citation
        # Citation offsets are offsets into the note itself.
        assert text[c.start_char:c.end_char] == mention.text
        assert report.native_extraction.resolve_citation(c) == mention.text
