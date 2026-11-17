"""Build timeline suggestions by pairing accepted events with dates in the same sentence.

Only accepted mentions are used. Sentences are slices of the preserved region text,
so every item can show its exact source. Nothing here guesses a date: unclear dates
stay flagged and the item goes to the review lane.
"""

from dataclasses import dataclass
from datetime import date
import re
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from ..schemas.entities import EntityLabel
from ..schemas.extraction import CitationReference
from ..schemas.layout import LayoutReport
from ..schemas.timeline import MentionRef, TimelineBuild, TimelineItemDraft
from .dates import ParsedDate, combine_range, parse_date

ABBREVIATIONS = ("dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "al", "no", "inc", "corp", "ltd", "fig", "eq", "e.g", "i.e")
_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])|\n\s*\n")
_RANGE_CONNECTOR = re.compile(r"^\s*(?:to|until|through|[–-])\s*$", re.IGNORECASE)
_BETWEEN_AND = re.compile(r"^\s*and\s*$", re.IGNORECASE)
_CHANGE = re.compile(r"\b(postponed|rescheduled|moved|delayed|pushed|brought forward)\b", re.IGNORECASE)
_BETWEEN = re.compile(r"\bbetween\b", re.IGNORECASE)
NAMESPACE = uuid5(NAMESPACE_URL, "digital-memory/timeline-item")


@dataclass(frozen=True)
class AcceptedMention:
    """An accepted mention with its effective (possibly corrected) type."""

    run_id: UUID
    mention_id: UUID
    label: EntityLabel
    text: str
    citation: CitationReference


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Exact (start, end) slices. Abbreviations such as Dr. or et al. do not end a sentence."""
    spans, start = [], 0
    for match in _BREAK.finditer(text):
        before = text[start:match.start()]
        last_word = re.findall(r"[A-Za-z.]+$", before)
        paragraph = match.group(0).count("\n") >= 2
        if not paragraph and last_word and last_word[0].rstrip(".").lower() in ABBREVIATIONS:
            continue
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _region_text(report: LayoutReport, citation: CitationReference) -> str | None:
    for run in (report.native_extraction, report.ocr_extraction):
        if run is None or run.id != citation.extraction_id:
            continue
        for page in run.pages:
            for region in page.regions:
                if region.id == citation.region_id:
                    return region.text
    return None


def _ref(mention: AcceptedMention, role: str) -> MentionRef:
    return MentionRef(run_id=mention.run_id, mention_id=mention.mention_id, role=role,
        label=mention.label, text=mention.text, citation=mention.citation)


def _key(mention: AcceptedMention) -> str:
    c = mention.citation
    return f"{c.region_id}:{c.start_char}:{c.end_char}"


def _choose_dates(sentence: str, offset: int, dates: list[tuple[AcceptedMention, ParsedDate]],
                  event: AcceptedMention) -> tuple[list[AcceptedMention], ParsedDate | None, list[str]]:
    """Pick the date(s) an event takes from its sentence, with explanatory flags."""
    if not dates:
        return [], None, []
    if len(dates) == 1:
        return [dates[0][0]], dates[0][1], []
    ordered = sorted(dates, key=lambda item: item[0].citation.start_char)
    first, second = ordered[0], ordered[1]
    between = sentence[first[0].citation.end_char - offset:second[0].citation.start_char - offset]
    both_resolved = first[1].status == "resolved" and second[1].status == "resolved"
    changed = bool(_CHANGE.search(sentence))
    joined = (_RANGE_CONNECTOR.match(between) or (_BETWEEN_AND.match(between) and _BETWEEN.search(sentence)))
    if len(ordered) == 2 and both_resolved and joined and changed:
        # "postponed from X to Y": the event now sits at Y.
        return [second[0]], second[1], ["date_changed_language"]
    if len(ordered) == 2 and both_resolved and joined:
        return [first[0], second[0]], combine_range(first[1], second[1]), ["date_range"]
    event_at = event.citation.start_char
    nearest = min(ordered, key=lambda item: (min(abs(item[0].citation.start_char - event_at),
        abs(item[0].citation.end_char - event_at)), item[0].citation.start_char))
    return [nearest[0]], nearest[1], ["multiple_dates_nearest_chosen"]


def build_timeline_items(report: LayoutReport, mentions: Sequence[AcceptedMention],
                         anchor: date | None = None) -> TimelineBuild:
    document_id = report.native_extraction.document_id
    by_region: dict[UUID, list[AcceptedMention]] = {}
    for mention in mentions:
        by_region.setdefault(mention.citation.region_id, []).append(mention)

    items: list[TimelineItemDraft] = []
    unattached = 0
    for region_mentions in by_region.values():
        text = _region_text(report, region_mentions[0].citation)
        if text is None:
            continue
        for start, end in sentence_spans(text):
            inside = [m for m in region_mentions if start <= m.citation.start_char and m.citation.end_char <= end]
            events = [m for m in inside if m.label == "event"]
            dates = [(m, parse_date(m.text, anchor)) for m in inside if m.label == "date"]
            if not events:
                unattached += len(dates)
                continue
            sentence = text[start:end]
            locations = [m for m in inside if m.label == "location"]
            participants = [m for m in inside if m.label in ("person", "organization", "project")]
            for event in sorted(events, key=lambda m: m.citation.start_char):
                chosen, parsed, flags = _choose_dates(sentence, start, dates, event)
                refs = [_ref(event, "event"), *(_ref(d, "date") for d in chosen),
                    *(_ref(m, "location") for m in locations), *(_ref(m, "participant") for m in participants)]
                key = f"{document_id}|{_key(event)}|{'+'.join(_key(d) for d in chosen) or '-'}"
                date_text = " – ".join(d.text for d in chosen) if chosen else None
                if parsed is None:
                    status, p_start, p_end, precision, all_flags = "none", None, None, None, flags
                else:
                    status, p_start, p_end, precision = parsed.status, parsed.start, parsed.end, parsed.precision
                    all_flags = [*parsed.flags, *flags]
                items.append(TimelineItemDraft(
                    id=uuid5(NAMESPACE, key), document_id=document_id,
                    title=re.sub(r"\s+", " ", event.text).strip(), page_number=event.citation.page_number,
                    sentence_text=sentence, sentence_start=start, sentence_end=end, date_text=date_text,
                    date_status=status, start_date=p_start, end_date=p_end, precision=precision,
                    flags=tuple(dict.fromkeys(all_flags)), anchor_date=anchor, refs=tuple(refs)))
    ordered = sorted(items, key=lambda item: (item.start_date is None, item.start_date or date.min,
        item.page_number, item.sentence_start, item.title))
    return TimelineBuild(items=tuple(ordered), unattached_dates=unattached)
