"""Reproducible development selection followed by held-out project evaluation."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backend.services.entity_mentions import extract_entity_mentions, load_model
from test_source_passages import report_for


DESCRIPTIONS = {
    "baseline": "A named project or initiative, not a generic task or subject",
    "explicit": "The proper name of a specific project, program, or initiative that people are working on. Include the word Project or Operation when it belongs to the name. Exclude generic work, tasks, technical components, architectures, models, products, and organizations.",
    "initiative": "A specifically named organized effort with an objective, such as a migration, rollout, or internal initiative. Extract the full project name including its Project or Operation prefix. The context must explicitly identify it as a project or initiative. Generic technical terms such as encoder, decoder, and attention are excluded.",
}
BASE_LABELS = {
    "person": "A named individual person, including authors and meeting participants",
    "organization": "A named company, institution, university, or organization",
}


def expected_spans(case):
    """Repeated names are annotated in occurrence order, using exact source text."""
    cursors = {}
    spans = set()
    for name in case["projects"]:
        start = case["text"].index(name, cursors.get(name, 0))
        end = start + len(name)
        spans.add((start, end))
        cursors[name] = end
    return spans


def measure(cases, report, result):
    correct = predicted = expected = 0
    negative_cases = correct_negative_cases = 0
    details = []
    other_mentions = {"person": 0, "organization": 0}
    for case, passage in zip(cases, result.passages, strict=True):
        gold = expected_spans(case)
        actual = set()
        names = {}
        for mention in passage.mentions:
            assert report.native_extraction.resolve_citation(mention.citation) == mention.text
            if mention.label == "project":
                span = (mention.start_char, mention.end_char)
                actual.add(span)
                names[span] = mention.text
            else:
                other_mentions[mention.label] += 1
        correct += len(gold & actual)
        predicted += len(actual)
        expected += len(gold)
        if not gold:
            negative_cases += 1
            correct_negative_cases += not actual and passage.status != "failed"
        details.append({"text": case["text"], "status": passage.status,
            "false_positives": [{"text": names[span], "span": span} for span in sorted(actual - gold)],
            "missed": [{"text": case["text"][start:end], "span": [start, end]}
                for start, end in sorted(gold - actual)]})
    return {"cases": len(cases), "expected": expected, "predicted": predicted,
        "correct": correct, "false_positives": predicted - correct,
        "missed": expected - correct,
        "precision": correct / predicted if predicted else None,
        "recall": correct / expected if expected else None,
        "f1": 2 * correct / (predicted + expected) if predicted + expected else None,
        "negative_cases": negative_cases, "correct_negative_cases": correct_negative_cases,
        "failed_passages": sum(page.status == "failed" for page in result.passages),
        "other_label_counts": other_mentions, "details": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["development", "evaluation"], required=True)
    parser.add_argument("--development-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    fixture = json.loads((ROOT / "tests/fixtures/project_labels.json").read_text(encoding="utf-8"))
    choices = list(DESCRIPTIONS)
    selected = None
    if args.phase == "evaluation":
        if args.development_report is None:
            parser.error("Evaluation requires --development-report to freeze selection")
        development = json.loads(args.development_report.read_text(encoding="utf-8"))
        if development["phase"] != "development" or development["descriptions"] != DESCRIPTIONS:
            parser.error("Development report configuration does not match")
        selected = development["selected"]
        choices = list(dict.fromkeys(["baseline", selected]))
    cases = fixture[args.phase]
    report = report_for([case["text"] for case in cases])
    model = load_model()
    measurements = {}
    for name in choices:
        result = extract_entity_mentions(report, model, library_version=version("gliner2"),
            labels={**BASE_LABELS, "project": DESCRIPTIONS[name]})
        metrics = measure(cases, report, result)
        measurements[name] = metrics
        print(name, json.dumps({key: value for key, value in metrics.items() if key != "details"}), flush=True)
        if metrics["failed_passages"]:
            raise RuntimeError("Evaluation contains failed inference passages")
    if args.phase == "development":
        selected = max(choices, key=lambda name: (
            measurements[name]["f1"] or 0, measurements[name]["precision"] or 0,
            -choices.index(name)))
    payload = {"phase": args.phase, "selected": selected,
        "selection_rule": "Highest development exact-span F1, then precision, then original configuration order",
        "annotation_policy": fixture["annotation_policy"], "descriptions": DESCRIPTIONS,
        "model_repository": result.model_repository, "model_revision": result.model_revision,
        "library_version": result.library_version, "threshold": result.threshold,
        "chunk_size": result.chunk_size, "chunk_overlap": result.chunk_overlap,
        "measurements": measurements}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
    print("Selected", selected)


if __name__ == "__main__":
    main()
