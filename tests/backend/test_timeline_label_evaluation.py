"""Validate the frozen timeline annotations without loading a model."""

import json
from pathlib import Path

from evaluate_entity_schemas import annotated_spans

FIXTURE = Path("tests/fixtures/timeline_labels.json")


def test_every_annotation_is_an_exact_unique_span_in_a_fresh_note():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    earlier = json.loads(Path("tests/fixtures/entity_schema_evaluation.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 41
    assert not {case["text"] for case in cases} & {case["text"] for case in earlier}
    for case in cases:
        spans = annotated_spans(case)
        assert len(spans) == sum(len(names) for names in case["entities"].values())
        for label, start, end in spans:
            assert case["text"][start:end].strip() == case["text"][start:end]


def test_set_covers_all_new_labels_and_includes_negatives():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    counts = {label: sum(len(case["entities"].get(label, [])) for case in cases)
        for label in ("date", "event", "location")}
    assert all(count >= 20 for count in counts.values()), counts
    assert any(not case["entities"] for case in cases)
