"""Exact sentence slices of preserved region text (shared by relation extraction).

The timeline service has an equivalent splitter; this module is standalone so the
two phases do not depend on each other and can be unified later.
"""

import re

ABBREVIATIONS = ("dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "al", "no", "inc", "corp", "ltd", "fig", "eq", "e.g", "i.e")
_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])|\n\s*\n")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) slices of `text`. Abbreviations such as Dr. or et al. do not end a sentence."""
    spans, start = [], 0
    for match in _BREAK.finditer(text):
        paragraph = match.group(0).count("\n") >= 2
        last_word = re.findall(r"[A-Za-z.]+$", text[start:match.start()])
        if not paragraph and last_word and last_word[0].rstrip(".").lower() in ABBREVIATIONS:
            continue
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]
