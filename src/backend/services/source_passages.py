"""Deterministic passage boundaries over unchanged, versioned source strings."""

import argparse
from pathlib import Path
import re
from uuid import uuid5

from ..schemas.extraction import CitationReference
from ..schemas.layout import LayoutReport
from ..schemas.passages import PassagePage, SourcePassage


ALGORITHM_VERSION = "source-passages-v1"
DEFAULT_MAX_CHARS = 1200
PARAGRAPH_BREAK = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)")
SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s+")
WORD_BREAK = re.compile(r"\s+")


def passage_spans(text: str, max_chars: int):
    """Partition without overlap or normalization, preferring readable boundaries."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            # Avoid tiny fragments when a boundary lies near the window start.
            minimum = max(1, max_chars // 2)
            for pattern in (PARAGRAPH_BREAK, SENTENCE_BREAK, WORD_BREAK):
                boundaries = [match.end() for match in pattern.finditer(window) if match.end() >= minimum]
                if boundaries:
                    end = start + boundaries[-1]
                    break
        if text[start:end].strip():
            yield start, end
        start = end


def build_source_passages(report: LayoutReport, *, max_chars: int = DEFAULT_MAX_CHARS):
    """Page order, then native and OCR regions; preserve distinct provenance."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    runs = [report.native_extraction]
    if report.ocr_extraction is not None:
        runs.append(report.ocr_extraction)
    if any(run.status in ("queued", "processing") for run in runs):
        raise ValueError("Passages require finalized extraction runs")
    layouts = {page.page_number: page for page in report.pages}
    run_pages = [{page.page_number: page for page in run.pages} for run in runs]
    passages = []
    skipped = 0
    for number in range(1, report.native_extraction.page_count + 1):
        layout = layouts[number]
        table_notes = [note for table in report.tables if table.page_number == number for note in table.warnings]
        for run, pages in zip(runs, run_pages):
            page = pages.get(number)
            if page is None:
                continue
            for region in page.regions:
                if (run.status == "failed" or page.status in ("pending", "failed")
                        or region.status == "failed" or region.method not in ("native", "ocr")
                        or region.kind not in ("text", "caption") or not region.text.strip()):
                    skipped += 1
                    continue
                warnings = list(dict.fromkeys([
                    *report.warnings, *run.warnings, *page.warnings,
                    *region.warnings, *layout.warnings, *table_notes,
                ]))
                review = bool(warnings or region.method == "ocr" or page.status != "processed"
                    or region.status != "extracted" or layout.status != "detected")
                for start, end in passage_spans(region.text, max_chars):
                    citation = CitationReference(document_id=run.document_id, extraction_id=run.id,
                        page_number=number, region_id=region.id, start_char=start, end_char=end)
                    identity = f"{ALGORITHM_VERSION}|{max_chars}|{region.id}|{start}|{end}"
                    passages.append(SourcePassage(id=uuid5(run.id, identity), ordinal=len(passages) + 1,
                        text=region.text[start:end], citation=citation, source_sha256=run.source_sha256,
                        method=region.method, review_required=review, warnings=tuple(warnings),
                        confidence=region.confidence))
    return tuple(passages), skipped


def passage_page(report: LayoutReport, *, limit: int = 20, offset: int = 0) -> PassagePage:
    if limit < 1 or offset < 0:
        raise ValueError("limit must be positive and offset nonnegative")
    passages, skipped = build_source_passages(report)
    return PassagePage(document_id=report.native_extraction.document_id,
        native_extraction_id=report.native_extraction.id,
        ocr_extraction_id=report.ocr_extraction.id if report.ocr_extraction else None,
        max_chars=DEFAULT_MAX_CHARS, total=len(passages), skipped_region_count=skipped,
        offset=offset, limit=limit, passages=passages[offset:offset + limit])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact source passages from a saved extraction report.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = LayoutReport.model_validate_json(args.input.read_text(encoding="utf-8"))
        passages, skipped = build_source_passages(report)
    except (OSError, ValueError):
        parser.exit(1, "Could not read a valid, finalized extraction report.\n")
    result = PassagePage(document_id=report.native_extraction.document_id,
        native_extraction_id=report.native_extraction.id,
        ocr_extraction_id=report.ocr_extraction.id if report.ocr_extraction else None,
        max_chars=DEFAULT_MAX_CHARS, total=len(passages), skipped_region_count=skipped,
        offset=0, limit=max(1, len(passages)), passages=passages)
    try:
        with args.output.open("x", encoding="utf-8") as target:
            target.write(result.model_dump_json(indent=2))
    except OSError:
        parser.exit(1, "Could not save passages. Choose a new output path in an existing directory.\n")
    print(f"Saved {result.total} passages; skipped {skipped} empty or ineligible regions.")


if __name__ == "__main__":
    main()
