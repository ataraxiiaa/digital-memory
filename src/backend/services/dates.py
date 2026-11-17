"""Conservative, offline normalization of date mentions into calendar ranges.

The parser never guesses. Bare weekdays, seasons, and day/month ties such as
12/04/2025 are reported as ambiguous or unsupported rather than resolved. A date
without a year takes the document date's year and is flagged `year_inferred`, so a
person can correct it. Relative dates (tomorrow, next week) need the document date.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Literal

Status = Literal["resolved", "needs_anchor", "ambiguous", "unparsed"]
Precision = Literal["day", "week", "month", "quarter", "year"]

MONTHS = {name: number for number, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
    ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
    ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
], 1) for name in names}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_MONTH = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_PART_OF_DAY = re.compile(r"\s+(morning|afternoon|evening|night)$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_RELATIVE_DAY = re.compile(r"^(today|yesterday|tomorrow)$")
_RELATIVE_UNIT = re.compile(r"^(this|next|last) (week|month|year)$")
_RELATIVE_WEEKDAY = re.compile(r"^(next|last) (" + "|".join(WEEKDAYS) + ")$")
_BARE_WEEKDAY = re.compile(r"^(" + "|".join(WEEKDAYS) + ")$")
_SEASON = re.compile(r"\b(spring|summer|autumn|fall|winter)\b")
_QUARTER = re.compile(r"^q([1-4])(?: (\d{4}))?$")
_WORD_QUARTER = re.compile(r"^(first|second|third|fourth) quarter(?: of (\d{4}))?$")
_YEAR_RANGE = re.compile(r"^(\d{4}) ?[–-] ?(\d{4})$")
_DAY_RANGE = re.compile(r"^" + _DAY + r" ?(?:to|until|through|-|–) ?" + _DAY + r" " + _MONTH + r"(?:,? (\d{4}))?$")
_DAY_MONTH = re.compile(r"^(?:the )?" + _DAY + r"(?: of)? " + _MONTH + r"(?:,? (\d{4}))?$")
_MONTH_DAY = re.compile(r"^" + _MONTH + r" " + _DAY + r"(?:,? (\d{4}))?$")
_MONTH_YEAR = re.compile(r"^" + _MONTH + r",? (\d{4})$")
_BARE_MONTH = re.compile(r"^" + _MONTH + r"$")
_YEAR = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class ParsedDate:
    status: Status
    start: date | None = None
    end: date | None = None
    precision: Precision | None = None
    flags: tuple[str, ...] = ()


def _unresolved(status: Status, *flags: str) -> ParsedDate:
    return ParsedDate(status, flags=tuple(flags))


def _valid_year(year: int) -> bool:
    return 1000 <= year <= 2999


def _day(year: int, month: int, day: int, flags: list[str]) -> ParsedDate:
    try:
        value = date(year, month, day)
    except ValueError:
        return _unresolved("unparsed", "invalid_date")
    return ParsedDate("resolved", value, value, "day", tuple(flags))


def _month_span(year: int, month: int, flags: list[str]) -> ParsedDate:
    return ParsedDate("resolved", date(year, month, 1), date(year, month, monthrange(year, month)[1]),
        "month", tuple(flags))


def _quarter(year: int, number: int, flags: list[str]) -> ParsedDate:
    first = 3 * number - 2
    return ParsedDate("resolved", date(year, first, 1), date(year, first + 2, monthrange(year, first + 2)[1]),
        "quarter", tuple(flags))


def _week(anchor: date, weeks: int, flags: list[str]) -> ParsedDate:
    monday = anchor - timedelta(days=anchor.weekday()) + timedelta(weeks=weeks)
    return ParsedDate("resolved", monday, monday + timedelta(days=6), "week", tuple(flags))


def parse_date(text: str, anchor: date | None = None) -> ParsedDate:
    """Normalize one date mention. `anchor` is the document's own date, if it has one."""
    value = re.sub(r"\s+", " ", text.strip().lower()).strip(".,;:")
    if not value:
        return _unresolved("unparsed")
    flags: list[str] = []
    stripped = _PART_OF_DAY.sub("", value)
    if stripped != value and stripped:
        value = stripped
        flags.append("part_of_day_ignored")

    if _SEASON.search(value):
        return _unresolved("unparsed", "season_unsupported")
    if match := _ISO.match(value):
        year, month, day = map(int, match.groups())
        return _day(year, month, day, flags) if _valid_year(year) else _unresolved("unparsed")
    if match := _SLASH.match(value):
        first, second, year = map(int, match.groups())
        if not _valid_year(year):
            return _unresolved("unparsed")
        if first > 12 and second <= 12:
            return _day(year, second, first, flags)
        if second > 12 and first <= 12:
            return _day(year, first, second, flags)
        if first == second:
            return _day(year, first, second, flags)
        return _unresolved("ambiguous", "ambiguous_day_month")

    if match := _BARE_WEEKDAY.match(value):
        return _unresolved("ambiguous", "bare_weekday", *flags)
    if match := _RELATIVE_DAY.match(value):
        if anchor is None:
            return _unresolved("needs_anchor")
        offset = {"today": 0, "yesterday": -1, "tomorrow": 1}[match.group(1)]
        shifted = anchor + timedelta(days=offset)
        return ParsedDate("resolved", shifted, shifted, "day", (*flags, "relative_date"))
    if match := _RELATIVE_UNIT.match(value):
        if anchor is None:
            return _unresolved("needs_anchor")
        which, unit = match.groups()
        step = {"this": 0, "next": 1, "last": -1}[which]
        marked = [*flags, "relative_date"]
        if unit == "week":
            return _week(anchor, step, marked)
        if unit == "month":
            index = anchor.year * 12 + anchor.month - 1 + step
            return _month_span(index // 12, index % 12 + 1, marked)
        year = anchor.year + step
        return ParsedDate("resolved", date(year, 1, 1), date(year, 12, 31), "year", tuple(marked))
    if match := _RELATIVE_WEEKDAY.match(value):
        if anchor is None:
            return _unresolved("needs_anchor")
        which, name = match.groups()
        target = WEEKDAYS[name]
        if which == "next":
            delta = (target - anchor.weekday() - 1) % 7 + 1
        else:
            delta = -((anchor.weekday() - target - 1) % 7 + 1)
        shifted = anchor + timedelta(days=delta)
        return ParsedDate("resolved", shifted, shifted, "day",
            (*flags, "relative_date", "weekday_direction_assumed"))

    def with_year(year_text: str | None):
        """Return (year, flags) or None when a missing year cannot be supplied."""
        if year_text is not None:
            return int(year_text), list(flags)
        if anchor is None:
            return None
        return anchor.year, [*flags, "year_inferred"]

    if match := _QUARTER.match(value) or _WORD_QUARTER.match(value):
        number = int(match.group(1)) if match.re is _QUARTER else ORDINALS[match.group(1)]
        resolved = with_year(match.group(2))
        if resolved is None:
            return _unresolved("needs_anchor")
        return _quarter(resolved[0], number, resolved[1]) if _valid_year(resolved[0]) else _unresolved("unparsed")
    if match := _YEAR_RANGE.match(value):
        first, last = int(match.group(1)), int(match.group(2))
        if _valid_year(first) and _valid_year(last) and first <= last:
            return ParsedDate("resolved", date(first, 1, 1), date(last, 12, 31), "year", tuple(flags))
        return _unresolved("unparsed")
    if match := _DAY_RANGE.match(value):
        first, last, month_name, year_text = match.groups()
        resolved = with_year(year_text)
        if resolved is None:
            return _unresolved("needs_anchor")
        month = MONTHS[month_name]
        start = _day(resolved[0], month, int(first), resolved[1])
        end = _day(resolved[0], month, int(last), resolved[1])
        if start.status != "resolved" or end.status != "resolved" or end.start < start.start:
            return _unresolved("unparsed", "invalid_date")
        return ParsedDate("resolved", start.start, end.start, "day", start.flags)
    if match := _DAY_MONTH.match(value):
        day, month_name, year_text = match.groups()
        resolved = with_year(year_text)
        if resolved is None:
            return _unresolved("needs_anchor")
        return _day(resolved[0], MONTHS[month_name], int(day), resolved[1])
    if match := _MONTH_YEAR.match(value):
        year = int(match.group(2))
        return _month_span(year, MONTHS[match.group(1)], list(flags)) if _valid_year(year) else _unresolved("unparsed")
    if match := _MONTH_DAY.match(value):
        month_name, day, year_text = match.groups()
        resolved = with_year(year_text)
        if resolved is None:
            return _unresolved("needs_anchor")
        return _day(resolved[0], MONTHS[month_name], int(day), resolved[1])
    if match := _BARE_MONTH.match(value):
        resolved = with_year(None)
        if resolved is None:
            return _unresolved("needs_anchor")
        return _month_span(resolved[0], MONTHS[match.group(1)], resolved[1])
    if match := _YEAR.match(value):
        year = int(match.group(1))
        if _valid_year(year):
            return ParsedDate("resolved", date(year, 1, 1), date(year, 12, 31), "year", tuple(flags))
    return _unresolved("unparsed")


def combine_range(first: ParsedDate, second: ParsedDate) -> ParsedDate:
    """Merge two resolved dates that a sentence joins as a range (from X to Y)."""
    if first.status != "resolved" or second.status != "resolved":
        raise ValueError("Only resolved dates can form a range")
    start = min(first.start, second.start)
    end = max(first.end, second.end)
    precision = first.precision if first.precision == second.precision else "day"
    return ParsedDate("resolved", start, end, precision, tuple(dict.fromkeys([*first.flags, *second.flags])))
