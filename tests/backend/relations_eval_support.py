"""Helpers: accepted mentions built from the gold entities of relations.json."""

import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.schemas.extraction import CitationReference
from backend.services.relations import AcceptedMention, build_relation_candidates
from evaluate_entity_schemas import annotated_spans
from test_source_passages import report_for

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/relations.json"


def load_cases(split: str | None = None) -> list[dict]:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if split in (None, case["split"])]


def gold_mentions(cases: list[dict]):
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
    return report, mentions


def predicted(build) -> set[tuple[int, str, str, str]]:
    return {(d.page_number, d.type, d.subject.text, d.object.text) for d in build.candidates}


def expected(cases: list[dict]) -> set[tuple[int, str, str, str]]:
    return {(number, kind, subject, obj) for number, case in enumerate(cases, 1)
        for kind, subject, obj in case["relations"]}


def run_split(cases: list[dict], model, **options):
    report, mentions = gold_mentions(cases)
    return build_relation_candidates(report, mentions, model, **options)


def metrics(cases: list[dict], build) -> dict:
    gold, found = expected(cases), predicted(build)
    correct = len(gold & found)
    by_type = {}
    for kind in sorted({item[1] for item in gold | found}):
        g = {item for item in gold if item[1] == kind}
        f = {item for item in found if item[1] == kind}
        right = len(g & f)
        by_type[kind] = {"expected": len(g), "predicted": len(f), "correct": right,
            "precision": right / len(f) if f else None, "recall": right / len(g) if g else None}
    return {"notes": len(cases), "expected": len(gold), "predicted": len(found), "correct": correct,
        "precision": correct / len(found) if found else None, "recall": correct / len(gold) if gold else None,
        "f1": 2 * correct / (len(gold) + len(found)) if gold or found else None,
        "false_positives": sorted(found - gold), "missed": sorted(gold - found),
        "counts": build.counts.model_dump(), "per_type": by_type}
