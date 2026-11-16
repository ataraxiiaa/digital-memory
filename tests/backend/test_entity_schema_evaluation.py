"""Validate all-label annotations and metrics for the expanded schema."""

import json
from pathlib import Path

from evaluate_entity_schemas import annotated_spans, span_metrics


def test_schema_fixture_spans_and_fresh_examples():
    fixture = json.loads(Path("tests/fixtures/entity_schema_evaluation.json").read_text(encoding="utf-8"))
    old = json.loads(Path("tests/fixtures/project_labels.json").read_text(encoding="utf-8"))
    old_texts = {case["text"] for split in ("development", "evaluation") for case in old[split]}
    for case in fixture["cases"]:
        assert case["text"] not in old_texts
        assert len(annotated_spans(case)) == sum(len(names) for names in case["entities"].values())


def test_wrong_type_is_counted_even_when_text_matches():
    metrics = span_metrics({("model", 0, 4)}, {("project", 0, 4)})
    assert metrics["correct"] == 0
    assert metrics["false_positives"] == metrics["missed"] == 1
