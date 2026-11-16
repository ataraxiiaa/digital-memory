"""Prepare a blank sheet for a second annotator and measure agreement with the first.

    python tests/backend/annotation_agreement.py sheet --output .cache/timeline-sheet.json
    python tests/backend/annotation_agreement.py compare .cache/timeline-sheet.json

The sheet has the same notes and annotation policy but empty entities. The second
annotator fills `entities` using only the policy, without seeing the reference or
any model output, then `compare` reports exact-span agreement per label.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate_entity_schemas import annotated_spans, span_metrics

REFERENCE = ROOT / "tests/fixtures/timeline_labels.json"
TIMELINE = ("date", "event", "location")


def make_sheet(reference: dict) -> dict:
    return {"annotation_policy": reference["annotation_policy"],
        "instructions": "Fill each case's entities as {label: [exact text, ...]} in reading order, one entry per occurrence. Labels: person, organization, project, model, method, dataset, component, date, event, location.",
        "cases": [{"text": case["text"], "entities": {}} for case in reference["cases"]]}


def agreement(reference: dict, other: dict) -> dict:
    if [c["text"] for c in reference["cases"]] != [c["text"] for c in other["cases"]]:
        raise ValueError("The two files must contain the same notes in the same order")
    expected, actual = set(), set()
    for number, (a, b) in enumerate(zip(reference["cases"], other["cases"], strict=True), 1):
        expected.update((number, *span) for span in annotated_spans(a))
        actual.update((number, *span) for span in annotated_spans(b))
    labels = [*TIMELINE, "person", "organization", "project", "model", "method", "dataset", "component"]
    result = {"all": span_metrics(expected, actual), "per_label": {label: span_metrics(
        {i for i in expected if i[1] == label}, {i for i in actual if i[1] == label}) for label in labels}}
    # Disagreements are the useful output: they show where the policy is ambiguous.
    texts = [c["text"] for c in reference["cases"]]
    result["disagreements"] = [{"note": texts[i[0] - 1], "label": i[1], "text": texts[i[0] - 1][i[2]:i[3]],
        "only_in": "reference" if i in expected else "second"} for i in sorted(expected ^ actual)]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sheet = commands.add_parser("sheet")
    sheet.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("annotated", type=Path)
    compare.add_argument("--output", type=Path)
    args = parser.parse_args()
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    if args.command == "sheet":
        if args.output.exists():
            parser.error("Choose a new output filename")
        args.output.write_text(json.dumps(make_sheet(reference), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote a blank sheet with {len(reference['cases'])} notes.")
        return
    result = agreement(reference, json.loads(args.annotated.read_text(encoding="utf-8")))
    summary = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in result["all"].items()}
    print("Exact-span agreement (F1 treats the reference as truth):", json.dumps(summary))
    for label, metrics in result["per_label"].items():
        if metrics["expected"] or metrics["predicted"]:
            print(f"  {label}: F1 {metrics['f1']:.2f} ({metrics['correct']} of {metrics['expected']} reference / {metrics['predicted']} second)")
    print(f"{len(result['disagreements'])} disagreements.")
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
