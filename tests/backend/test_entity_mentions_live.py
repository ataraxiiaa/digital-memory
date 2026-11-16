"""Opt-in local-model evaluation; no source text is sent to a hosted model."""

import json
import os
from pathlib import Path

import pytest

from backend.services.entity_mentions import extract_entity_mentions, load_model
from test_source_passages import report_for


@pytest.mark.skipif(os.getenv("DIGITAL_MEMORY_RUN_ENTITY_TESTS") != "1",
    reason="Explicit opt-in required for local model download and inference")
def test_real_model_synthetic_notes():
    cases = json.loads(Path("tests/fixtures/entity_notes.json").read_text(encoding="utf-8"))
    source = report_for([case["text"] for case in cases])
    result = extract_entity_mentions(source, load_model())
    assert all(page.status != "failed" for page in result.passages)
    expected = set()
    for page, case in enumerate(cases, 1):
        for label, names in case["entities"].items():
            for name in names:
                start = case["text"].index(name)
                expected.add((page, label, start, start + len(name)))
    actual = set()
    for page in result.passages:
        for mention in page.mentions:
            assert source.native_extraction.resolve_citation(mention.citation) == mention.text
            actual.add((mention.citation.page_number, mention.label,
                mention.citation.start_char, mention.citation.end_char))
    correct = len(expected & actual)
    metrics = {"cases": len(cases), "expected_mentions": len(expected),
        "predicted_mentions": len(actual), "correct_exact_spans_and_labels": correct,
        "precision": correct / len(actual) if actual else 0,
        "recall": correct / len(expected) if expected else 0,
        "false_positives": sorted(actual - expected), "missed": sorted(expected - actual)}
    Path(".cache").mkdir(exist_ok=True)
    Path(".cache/entity-synthetic-evaluation.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    Path(".cache/entity-synthetic-mentions.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps(metrics))
    # Accuracy is measured, not asserted against an unestablished benchmark.
