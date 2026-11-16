"""Guard occurrence annotations and the exact-span scoring calculation."""

import json
from pathlib import Path

from backend.services.entity_mentions import extract_entity_mentions
from evaluate_project_labels import expected_spans, measure
from test_entity_mentions import FakeModel, candidate
from test_source_passages import report_for


def test_annotation_occurrences_and_disjoint_splits():
    fixture = json.loads(Path("tests/fixtures/project_labels.json").read_text(encoding="utf-8"))
    development = {case["text"] for case in fixture["development"]}
    evaluation = {case["text"] for case in fixture["evaluation"]}
    assert development.isdisjoint(evaluation)
    for case in fixture["development"] + fixture["evaluation"]:
        spans = expected_spans(case)
        assert len(spans) == len(case["projects"])
        assert sorted(case["text"][start:end] for start, end in spans) == sorted(case["projects"])


def test_boundary_error_counts_as_extra_and_missed_and_citations_still_resolve():
    cases = [{"text": "Project Atlas", "projects": ["Project Atlas"]}]
    report = report_for([cases[0]["text"]])
    model = FakeModel({"entities": {"project": [candidate("Atlas", 8, 13)]}})
    metrics = measure(cases, report, extract_entity_mentions(report, model))
    assert metrics["correct"] == 0
    assert metrics["false_positives"] == metrics["missed"] == 1
    assert metrics["precision"] == metrics["recall"] == 0


def test_no_predictions_has_undefined_precision_and_correct_negative_case():
    cases = [{"text": "Generic task", "projects": []}]
    report = report_for([cases[0]["text"]])
    result = extract_entity_mentions(report, FakeModel({"entities": {}}))
    metrics = measure(cases, report, result)
    assert metrics["precision"] is None
    assert metrics["correct_negative_cases"] == 1
