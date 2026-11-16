"""Evaluate date, event, and location suggestions on a frozen annotated set.

The fixture was authored before any run and is not used to tune descriptions.
"""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backend.services.entity_mentions import LABEL_SCHEMAS, TIMELINE_LABELS, extract_entity_mentions, load_model
from evaluate_entity_schemas import annotated_spans, span_metrics
from test_source_passages import report_for

FIXTURE = ROOT / "tests/fixtures/timeline_labels.json"


def measure(cases, source, result):
    expected, actual, details = set(), set(), []
    for number, (case, passage) in enumerate(zip(cases, result.passages, strict=True), 1):
        gold = annotated_spans(case)
        predicted = set()
        for mention in passage.mentions:
            assert source.native_extraction.resolve_citation(mention.citation) == mention.text
            predicted.add((mention.label, mention.start_char, mention.end_char))
        expected.update((number, *span) for span in gold)
        actual.update((number, *span) for span in predicted)
        details.append({"text": case["text"], "status": passage.status,
            "extra": [(label, case["text"][s:e]) for label, s, e in sorted(predicted - gold)],
            "missed": [(label, case["text"][s:e]) for label, s, e in sorted(gold - predicted)]})
    labels = list(LABEL_SCHEMAS["full-v1"])
    timeline = set(TIMELINE_LABELS)
    metrics = {
        "all_labels": span_metrics(expected, actual),
        "timeline_labels": span_metrics({i for i in expected if i[1] in timeline}, {i for i in actual if i[1] in timeline}),
        "other_labels": span_metrics({i for i in expected if i[1] not in timeline}, {i for i in actual if i[1] not in timeline}),
        "per_label": {label: span_metrics({i for i in expected if i[1] == label}, {i for i in actual if i[1] == label})
            for label in labels},
        "failed_passages": sum(page.status == "failed" for page in result.passages),
    }
    # Lenient view: a prediction overlapping a same-label annotation counts as boundary-only.
    boundary = 0
    for number, case in enumerate(cases, 1):
        gold = annotated_spans(case)
        predicted = {(m.label, m.start_char, m.end_char) for m in result.passages[number - 1].mentions}
        for label, start, end in predicted - gold:
            if label in timeline and any(g[0] == label and g[1] < end and start < g[2] for g in gold):
                boundary += 1
    metrics["timeline_boundary_only_errors"] = boundary
    metrics["details"] = details
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    raw = FIXTURE.read_bytes()
    fixture = json.loads(raw)
    cases = fixture["cases"]
    source = report_for([case["text"] for case in cases])
    model = load_model()
    measurements = {}
    for name in ("research-v1", "full-v1"):
        result = extract_entity_mentions(source, model, labels=LABEL_SCHEMAS[name], library_version=version("gliner2"))
        measurements[name] = measure(cases, source, result)
        print(name, json.dumps({k: v for k, v in measurements[name].items() if k != "details"}), flush=True)
    payload = {"cases": len(cases), "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "annotation_policy": fixture["annotation_policy"], "model_revision": result.model_revision,
        "library_version": result.library_version, "threshold": result.threshold,
        "schemas": {name: LABEL_SCHEMAS[name] for name in measurements}, "measurements": measurements}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
    if any(m["failed_passages"] for m in measurements.values()):
        raise SystemExit("Evaluation contains failed inference passages")


if __name__ == "__main__":
    main()
