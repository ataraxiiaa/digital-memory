"""Measure the name matcher on tests/fixtures/name_variant_pairs.json (no model needed).

A pair counts as "suggested for merging" when the matcher returns kind exact or
initial. Weak kinds (surname, related) are reported separately because the UI
shows them as low-confidence suggestions.
"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backend.services.entity_resolution import match_names

STRONG = ("exact", "initial")


def evaluate(pairs):
    out = {}
    for group, kinds in (("person", ("person",)), ("organization", ("organization",)),
                         ("other", ("location", "method", "date"))):
        rows = [p for p in pairs if p["type"] in kinds]
        result = [(p, match_names(p["type"], p["a"], p["b"])) for p in rows]
        strong = lambda found: found is not None and found.kind in STRONG
        same = [(p, f) for p, f in result if p["gold"] == "same"]
        different = [(p, f) for p, f in result if p["gold"] == "different"]
        possible = [(p, f) for p, f in result if p["gold"] == "possible"]
        out[group] = {
            "same_pairs": len(same),
            "same_suggested_strong": sum(strong(f) for _, f in same),
            "same_suggested_any": sum(f is not None for _, f in same),
            "same_missed": [f"{p['a']} / {p['b']}" for p, f in same if f is None],
            "different_pairs": len(different),
            "different_strongly_suggested": [f"{p['a']} / {p['b']}" for p, f in different if strong(f)],
            "different_weakly_suggested": [f"{p['a']} / {p['b']}" for p, f in different
                                           if f is not None and not strong(f)],
            "possible_pairs": len(possible),
            "possible_suggested": [f"{p['a']} / {p['b']} ({f.kind})" for p, f in possible if f is not None],
        }
        tp = out[group]["same_suggested_strong"]
        fp = len(out[group]["different_strongly_suggested"])
        out[group]["strong_precision_vs_different"] = tp / (tp + fp) if tp + fp else None
        out[group]["strong_recall_on_same"] = tp / len(same) if same else None
    return out


if __name__ == "__main__":
    fixture = json.loads((ROOT / "tests/fixtures/name_variant_pairs.json").read_text(encoding="utf-8"))
    print(json.dumps(evaluate(fixture["pairs"]), ensure_ascii=False, indent=2))
