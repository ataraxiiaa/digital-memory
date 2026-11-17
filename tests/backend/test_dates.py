"""Date normalization against expectations authored before the parser existed."""

from datetime import date
import json
from pathlib import Path

import pytest

from backend.services.dates import combine_range, parse_date

FIXTURE = json.loads(Path("tests/fixtures/date_normalization.json").read_text(encoding="utf-8"))
DEFAULT_ANCHOR = date.fromisoformat(FIXTURE["anchor"])


def label(case):
    return f"{case['text']}|{case.get('anchor', 'anchor')}"


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=label)
def test_normalization_matches_authored_expectation(case):
    anchor = DEFAULT_ANCHOR if "anchor" not in case else (date.fromisoformat(case["anchor"]) if case["anchor"] else None)
    parsed = parse_date(case["text"], anchor)
    assert parsed.status == case["status"]
    assert (parsed.start.isoformat() if parsed.start else None) == case["start"]
    assert (parsed.end.isoformat() if parsed.end else None) == case["end"]
    assert parsed.precision == case["precision"]
    assert sorted(parsed.flags) == sorted(case["flags"])


def test_anchor_year_boundaries_and_leap_days():
    january = date(2026, 1, 3)
    assert parse_date("last month", january).start == date(2025, 12, 1)
    assert parse_date("next month", date(2026, 12, 20)).start == date(2027, 1, 1)
    assert parse_date("29 February 2024").start == date(2024, 2, 29)
    assert parse_date("29 February 2025").flags == ("invalid_date",)
    assert parse_date("next week", date(2026, 12, 30)).end == date(2027, 1, 10)


def test_weekday_directions_never_return_the_anchor_itself():
    wednesday = date(2026, 11, 18)
    assert parse_date("next Wednesday", wednesday).start == date(2026, 11, 25)
    assert parse_date("last Wednesday", wednesday).start == date(2026, 11, 11)


@pytest.mark.parametrize("text", ["", "   ", "2", "99999", "13/13/2025", "0 March 2024", "Q5", "40 June 2024", "6 to 4 June 2025"])
def test_garbage_is_never_resolved(text):
    assert parse_date(text, DEFAULT_ANCHOR).status in ("unparsed", "ambiguous")


def test_case_whitespace_and_punctuation_are_ignored():
    assert parse_date("  3   MARCH,  2024. ").start == date(2024, 3, 3)


def test_combine_range_merges_and_keeps_flags():
    start, end = parse_date("2 May", DEFAULT_ANCHOR), parse_date("14 May", DEFAULT_ANCHOR)
    merged = combine_range(start, end)
    assert (merged.start, merged.end, merged.precision) == (date(2026, 5, 2), date(2026, 5, 14), "day")
    assert merged.flags == ("year_inferred",)
    with pytest.raises(ValueError):
        combine_range(start, parse_date("Monday"))
