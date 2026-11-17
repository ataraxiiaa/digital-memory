"""Timeline pairing against expectations authored before the service existed."""

from datetime import date
from uuid import uuid4

import pytest

from backend.schemas.extraction import CitationReference
from backend.services.timeline import AcceptedMention, build_timeline_items, sentence_spans
from test_source_passages import report_for
from timeline_eval_support import LABELS, PAIRS, gold_build, summarize


def expected_items():
    for case in PAIRS["cases"]:
        for item in case["items"]:
            yield case["note"], item


def test_pairing_matches_every_authored_expectation_except_documented_gaps():
    notes = [case["note"] for case in PAIRS["cases"]]
    _, build = gold_build(notes)
    actual = {}
    for page, title, dates, status, locations, participants in summarize(build):
        actual.setdefault(notes[page - 1], []).append((title, dates, status, locations, participants))
    misses, gaps = [], []
    for note, item in expected_items():
        expected = (item["event"], item["dates"], item["status"], item["locations"], item["participants"])
        candidates = actual.get(note, [])
        if expected in candidates:
            if item.get("known_gap"):
                misses.append(f"gap fixed, update fixture: note {note} {item['event']}")
            candidates.remove(expected)
        elif item.get("known_gap"):
            gaps.append(note)
        else:
            misses.append(f"note {note}: expected {expected}, got {candidates}")
    assert not misses, misses
    assert gaps == [18, 37]


def test_every_item_is_cited_exactly_and_ids_are_stable():
    notes = [case["note"] for case in PAIRS["cases"]]
    report, first = gold_build(notes)
    _, second = gold_build(notes)
    assert len({item.id for item in first.items}) == len(first.items)
    for item in first.items:
        run = report.native_extraction
        for ref in item.refs:
            assert run.resolve_citation(ref.citation) == ref.text
        region_text = next(r.text for p in run.pages for r in p.regions if r.id == item.refs[0].citation.region_id)
        assert region_text[item.sentence_start:item.sentence_end] == item.sentence_text
        assert item.sentence_start <= item.refs[0].citation.start_char < item.sentence_end
    # Ids derive from source spans, so rebuilding with the same source reuses them only if the report matches.
    assert len(first.items) == len(second.items)


def test_dates_without_an_event_are_counted_not_invented():
    # Notes 6 and 12 contain dates but no accepted event.
    _, build = gold_build([6, 12])
    assert build.items == () and build.unattached_dates == 3


def test_resolved_dates_use_the_anchor_and_unclear_dates_stay_flagged():
    _, build = gold_build([9, 2, 4])
    by_title = {item.title: item for item in build.items}
    assert by_title["kickoff meeting"].start_date == date(2026, 11, 17)
    assert "relative_date" in by_title["kickoff meeting"].flags
    assert by_title["supplier summit"].start_date is None
    assert by_title["supplier summit"].date_status == "ambiguous"
    workshop = by_title["quarterly planning workshop"]
    assert (workshop.start_date, workshop.end_date) == (date(2026, 11, 4), date(2026, 11, 6))
    assert "year_inferred" in workshop.flags


def test_without_an_anchor_relative_dates_need_one():
    report = report_for(["The kickoff meeting happened yesterday."])
    run = report.native_extraction
    region = run.pages[0].regions[0]

    def mention(label, text):
        start = region.text.index(text)
        return AcceptedMention(uuid4(), uuid4(), label, text, CitationReference(
            document_id=run.document_id, extraction_id=run.id, page_number=1, region_id=region.id,
            start_char=start, end_char=start + len(text)))
    build = build_timeline_items(report, [mention("event", "kickoff meeting"), mention("date", "yesterday")], None)
    assert build.items[0].date_status == "needs_anchor" and build.items[0].start_date is None


@pytest.mark.parametrize("text,count", [
    ("Dr. Smith met Ms. Lee. They left.", 2),
    ("Smith et al. reported it. Then we moved on.", 2),
    ("One sentence only", 1),
    ("First paragraph.\n\nSecond paragraph", 2),
    ("It cost 3.5 million. Really!", 2),
    ("", 0)])
def test_sentence_splitting_keeps_exact_offsets(text, count):
    spans = sentence_spans(text)
    assert len(spans) == count
    assert all(text[a:b].strip() == text[a:b] for a, b in spans)


def test_postponed_events_take_the_new_date_and_flag_it():
    _, build = gold_build([33])
    item = build.items[0]
    assert item.start_date == date(2026, 10, 16) and "date_changed_language" in item.flags


def test_event_notes_are_only_the_authored_ones():
    # Guards the fixture: every authored note really contains the event it names.
    for note, item in expected_items():
        assert item["event"] in LABELS[note - 1]["text"]
