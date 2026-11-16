"""Agreement tooling between a reference and a second annotator."""

import json
from pathlib import Path

import pytest

from annotation_agreement import REFERENCE, agreement, make_sheet


def reference():
    return json.loads(Path(REFERENCE).read_text(encoding="utf-8"))


def test_sheet_hides_reference_answers():
    sheet = make_sheet(reference())
    assert all(case["entities"] == {} for case in sheet["cases"])
    assert len(sheet["cases"]) == 41


def test_identical_annotations_agree_fully_and_changes_are_reported():
    ref = reference()
    assert agreement(ref, ref)["all"]["f1"] == 1.0
    other = json.loads(json.dumps(ref))
    other["cases"][0]["entities"]["location"] = []
    result = agreement(ref, other)
    assert result["per_label"]["location"]["missed"] == 1
    assert result["disagreements"] == [{"note": ref["cases"][0]["text"], "label": "location",
        "text": "Karachi", "only_in": "reference"}]


def test_different_notes_are_rejected():
    other = reference()
    other["cases"][0]["text"] += " Extra."
    with pytest.raises(ValueError, match="same notes"):
        agreement(reference(), other)
