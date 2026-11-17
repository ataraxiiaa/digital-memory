"""Measure event/date pairing on the authored notes, from gold mentions and from real model output.

The model run treats every suggestion as if a person accepted it, so it shows the
ceiling of the full pipeline before review. Truth comes from timeline_pairs.json.
"""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.services.entity_mentions import FULL_LABELS, extract_entity_mentions, load_model
from backend.services.timeline import AcceptedMention, build_timeline_items
from timeline_eval_support import ANCHOR, LABELS, PAIRS, gold_build, summarize
from test_source_passages import report_for


def expected_by_note():
    expected = {}
    for case in PAIRS["cases"]:
        expected[case["note"]] = [(i["event"], i["dates"], i["status"], i["locations"], i["participants"]) for i in case["items"]]
    return expected


def score(actual_by_note, expected):
    correct = extra = missing = 0
    problems = []
    for note, wanted in expected.items():
        got = list(actual_by_note.get(note, []))
        for item in wanted:
            if item in got:
                got.remove(item)
                correct += 1
            else:
                missing += 1
                problems.append({"note": note, "expected": item, "got": [g for g in got if g[0] == item[0]]})
        extra += len(got)
    total = sum(len(v) for v in expected.values())
    return {"expected_items": total, "exactly_right": correct, "missing_or_wrong": missing, "extra_items": extra,
        "accuracy": round(correct / total, 3), "problems": problems}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    expected = expected_by_note()
    notes = sorted(expected)

    _, build = gold_build(notes)
    gold = {}
    for page, title, dates, status, locations, participants in summarize(build):
        gold.setdefault(notes[page - 1], []).append((title, dates, status, locations, participants))

    # Real model on the same notes (each note a page), every suggestion treated as accepted.
    cases = [LABELS[n - 1] for n in notes]
    report = report_for([c["text"] for c in cases])
    result = extract_entity_mentions(report, load_model(), labels=FULL_LABELS, library_version=version("gliner2"))
    run = report.native_extraction
    mentions = [AcceptedMention(run_id=uuid4(), mention_id=m.id, label=m.label, text=m.text, citation=m.citation)
        for passage in result.passages for m in passage.mentions]
    model_build = build_timeline_items(report, mentions, ANCHOR)
    pipeline = {}
    for page, title, dates, status, locations, participants in summarize(model_build):
        pipeline.setdefault(notes[page - 1], []).append((title, dates, status, locations, participants))

    payload = {"anchor": str(ANCHOR), "fixture_notes": len(notes),
        "from_gold_mentions": score(gold, expected), "from_model_mentions": score(pipeline, expected)}
    for key in ("from_gold_mentions", "from_model_mentions"):
        print(key, {k: v for k, v in payload[key].items() if k != "problems"})
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
