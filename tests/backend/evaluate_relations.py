"""Measure relation extraction on the annotated notes, one split at a time.

    python tests/backend/evaluate_relations.py --split dev --output .cache/relations-dev-v1.json
    python tests/backend/evaluate_relations.py --split evaluation --output .cache/relations-evaluation-v1.json

Tune only against `dev`. Read `evaluation` after the configuration is frozen.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.services.entity_mentions import MODEL_REVISION, load_model
from relations_eval_support import FIXTURE, load_cases, metrics, run_split


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("dev", "evaluation"), required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    cases = load_cases(args.split)
    build = run_split(cases, load_model(), threshold=args.threshold)
    result = metrics(cases, build)
    payload = {"split": args.split, "threshold": args.threshold, "model_revision": MODEL_REVISION,
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), **result}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in result.items()
        if k in ("notes", "expected", "predicted", "correct", "precision", "recall", "f1")}
    print(args.split, json.dumps(summary))
    print("counts", json.dumps(result["counts"]))
    for kind, item in result["per_type"].items():
        print(" ", kind, json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in item.items()}))
    print("false positives:", result["false_positives"])
    print("missed:", result["missed"])


if __name__ == "__main__":
    main()
