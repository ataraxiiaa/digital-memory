"""Shared helpers: build accepted mentions from the gold annotations of timeline_labels.json."""

from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.schemas.extraction import CitationReference
from backend.services.timeline import AcceptedMention, build_timeline_items
from evaluate_entity_schemas import annotated_spans
from test_source_passages import report_for
from uuid import uuid4

LABELS = json.loads(Path("tests/fixtures/timeline_labels.json").read_text(encoding="utf-8"))["cases"]
PAIRS = json.loads(Path("tests/fixtures/timeline_pairs.json").read_text(encoding="utf-8"))
ANCHOR = date.fromisoformat(PAIRS["anchor"])


def gold_build(notes: list[int]):
    """Return (report, build) for the chosen 1-based notes, each on its own page."""
    cases = [LABELS[number - 1] for number in notes]
    report = report_for([case["text"] for case in cases])
    run = report.native_extraction
    mentions = []
    for page, case in zip(run.pages, cases):
        region = page.regions[0]
        for label, start, end in sorted(annotated_spans(case), key=lambda span: span[1]):
            mentions.append(AcceptedMention(run_id=uuid4(), mention_id=uuid4(), label=label,
                text=case["text"][start:end],
                citation=CitationReference(document_id=run.document_id, extraction_id=run.id,
                    page_number=page.page_number, region_id=region.id, start_char=start, end_char=end)))
    return report, build_timeline_items(report, mentions, ANCHOR)


def summarize(build):
    """Items as (page, event, date texts, status, locations, participants)."""
    result = []
    for item in build.items:
        roles = {}
        for ref in item.refs:
            roles.setdefault(ref.role, []).append(ref.text)
        result.append((item.page_number, item.title, roles.get("date", []), item.date_status,
            roles.get("location", []), roles.get("participant", [])))
    return result
