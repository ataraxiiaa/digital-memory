"""Search indexing payloads and safe snippet construction.

Search itself runs in Postgres. This module only prepares passages from a saved
extraction and turns the database's marker-wrapped snippet into highlight segments,
so no markup from source text can ever reach a browser as HTML.
"""

import re

from ..schemas.layout import LayoutReport
from ..schemas.search import SnippetSegment
from .source_passages import build_source_passages

START, STOP = chr(0xE000), chr(0xE001)  # private-use markers inserted by ts_headline
MAX_QUERY_CHARS = 200


def passage_rows(report: LayoutReport) -> tuple[list[dict], int]:
    """Searchable rows (with exact citations) and the number of ineligible regions skipped."""
    passages, skipped = build_source_passages(report)
    rows = [{
        "id": str(p.id), "page_number": p.citation.page_number, "region_id": str(p.citation.region_id),
        "start_char": p.citation.start_char, "end_char": p.citation.end_char, "method": p.method,
        "text": p.text, "source_sha256": p.source_sha256, "review_required": p.review_required,
        "citation": p.citation.model_dump(mode="json"),
    } for p in passages]
    return rows, skipped


def snippet_segments(marked: str | None) -> list[SnippetSegment]:
    """Split a marker-wrapped snippet into segments. Stray or unbalanced markers are dropped."""
    if not marked:
        return []
    segments, inside = [], False
    for part in re.split(f"([{START}{STOP}])", marked):
        if part == START:
            inside = True
        elif part == STOP:
            inside = False
        elif part:
            segments.append(SnippetSegment(text=part, match=inside))
    return segments


def query_words(query: str) -> list[str]:
    """Plain words used for highlighting names, matching the database's word-contains rule."""
    return [word for word in re.split(r"\s+", query.strip().lower()) if word]


def highlight_words(text: str, words: list[str]) -> list[SnippetSegment]:
    """Mark every case-insensitive occurrence of any query word inside a short string."""
    if not text:
        return []
    lowered = text.lower()
    if len(lowered) != len(text):  # case folding changed lengths: highlight nothing rather than misalign
        return [SnippetSegment(text=text)]
    marks = [False] * len(text)
    for word in words:
        start = lowered.find(word)
        while word and start != -1:
            for index in range(start, start + len(word)):
                marks[index] = True
            start = lowered.find(word, start + len(word))
    segments, begin = [], 0
    for index in range(1, len(text) + 1):
        if index == len(text) or marks[index] != marks[begin]:
            segments.append(SnippetSegment(text=text[begin:index], match=marks[begin]))
            begin = index
    return segments
