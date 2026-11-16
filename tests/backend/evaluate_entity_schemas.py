"""Compare frozen core and research schemas on fresh, fully annotated notes."""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backend.services.entity_mentions import LABEL_SCHEMAS, extract_entity_mentions, load_model
from test_source_passages import report_for


def annotated_spans(case):
    spans = set()
    for label, names in case["entities"].items():
        cursor = {}
        for name in names:
            start = case["text"].index(name, cursor.get(name, 0))
            end = start + len(name)
            spans.add((label, start, end))
            cursor[name] = end
    return spans


def span_metrics(expected, actual):
    correct = len(expected & actual)
    return {"expected": len(expected), "predicted": len(actual), "correct": correct,
        "false_positives": len(actual - expected), "missed": len(expected - actual),
        "precision": correct / len(actual) if actual else None,
        "recall": correct / len(expected) if expected else None,
        "f1": 2 * correct / (len(expected) + len(actual)) if expected or actual else None}


def measure(cases, source, result):
    expected = set()
    actual = set()
    details = []
    for number, (case, passage) in enumerate(zip(cases, result.passages, strict=True), 1):
        gold = annotated_spans(case)
        predicted = set()
        for mention in passage.mentions:
            assert source.native_extraction.resolve_citation(mention.citation) == mention.text
            predicted.add((mention.label, mention.start_char, mention.end_char))
        expected.update((number, *span) for span in gold)
        actual.update((number, *span) for span in predicted)
        details.append({"text": case["text"], "status": passage.status,
            "predictions": [{"label": label, "text": case["text"][start:end],
                "start": start, "end": end} for label, start, end in sorted(predicted)],
            "extra": sorted(predicted - gold), "missed": sorted(gold - predicted)})
    metrics = {"all_labels": span_metrics(expected, actual)}
    metrics["shared_labels"] = span_metrics(
        {item for item in expected if item[1] in LABEL_SCHEMAS["core-v1"]},
        {item for item in actual if item[1] in LABEL_SCHEMAS["core-v1"]})
    metrics["per_label"] = {label: span_metrics(
        {item for item in expected if item[1] == label},
        {item for item in actual if item[1] == label}) for label in LABEL_SCHEMAS["research-v1"]}
    metrics["failed_passages"] = sum(page.status == "failed" for page in result.passages)
    metrics["details"] = details
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    fixture_bytes = (ROOT / "tests/fixtures/entity_schema_evaluation.json").read_bytes()
    fixture = json.loads(fixture_bytes)
    cases = fixture["cases"]
    source = report_for([case["text"] for case in cases])
    model = load_model()
    measurements = {}
    for name, labels in LABEL_SCHEMAS.items():
        result = extract_entity_mentions(source, model, labels=labels,
            library_version=version("gliner2"))
        measurements[name] = measure(cases, source, result)
        print(name, json.dumps({key: value for key, value in measurements[name].items()
            if key != "details"}), flush=True)
    payload = {"cases": len(cases), "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "annotation_policy": fixture["annotation_policy"],
        "model_repository": result.model_repository, "model_revision": result.model_revision,
        "library_version": result.library_version, "threshold": result.threshold,
        "chunk_size": result.chunk_size, "chunk_overlap": result.chunk_overlap,
        "schemas": LABEL_SCHEMAS, "measurements": measurements}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
    if any(metrics["failed_passages"] for metrics in measurements.values()):
        raise SystemExit("Evaluation contains failed inference passages")


if __name__ == "__main__":
    main()
