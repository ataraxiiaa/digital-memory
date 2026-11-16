"""Model-independent checks of evidence validation and failure reporting."""

import pytest

from backend.services.entity_mentions import CORE_LABELS, LABELS, RESEARCH_LABELS, extract_entity_mentions
from backend.schemas.entities import EntityReport
from test_source_passages import report_for, add_ocr


class FakeModel:
    def __init__(self, result):
        self.result = result

    def extract_entities_long(self, text, labels, **options):
        assert options["include_spans"] and options["include_confidence"]
        assert options["chunk_overlap"] > 0
        return self.result


def candidate(text="Sara", start=0, end=4, confidence=.9):
    return dict(text=text, start=start, end=end, confidence=confidence)


def test_exact_citations_stable_ids_and_duplicate_removal():
    source = report_for(["Sara approved Project Atlas."])
    model = FakeModel({"entities": {"person": [candidate(), candidate()]}})
    first = extract_entity_mentions(source, model)
    again = extract_entity_mentions(source, model)
    mention, = first.passages[0].mentions
    assert first == again
    assert source.native_extraction.resolve_citation(mention.citation) == "Sara"
    assert mention.status == "suggested"
    changed = extract_entity_mentions(source, model, threshold=.6)
    assert changed.passages[0].mentions[0].id != mention.id


@pytest.mark.parametrize("bad", [candidate(start=-1), candidate(end=500),
    candidate(text="Sarah"), candidate(start=True), candidate(confidence=float("nan")),
    candidate(confidence=1.1), candidate(confidence=.1), {}, None])
def test_invalid_candidates_are_rejected(bad):
    result = extract_entity_mentions(report_for(["Sara approved it."]),
        FakeModel({"entities": {"person": [bad]}})).passages[0]
    assert result.mentions == ()
    assert result.rejected_candidate_count == 1
    assert result.status == "needs_review"


@pytest.mark.parametrize("raw", [{}, {"entities": []}, {"entities": {"alien": []}},
    {"entities": {"person": "Sara"}}])
def test_invalid_response_is_failure_not_empty_success(raw):
    result = extract_entity_mentions(report_for(["Sara"]), FakeModel(raw)).passages[0]
    assert result.status == "failed"


def test_empty_success_and_ocr_review_are_distinct():
    source = add_ocr(report_for(["No names here."]), "No names here.")
    result = extract_entity_mentions(source, FakeModel({"entities": {}}))
    assert result.passages[0].status == "completed"
    assert result.passages[1].status == "needs_review"


def test_offsets_account_for_later_passage_and_unicode():
    text = "🙂 " + "a " * 650 + "Sara"
    source = report_for([text])
    class Model:
        def extract_entities_long(self, text, *args, **kwargs):
            start = text.find("Sara")
            return {"entities": {"person": [] if start < 0 else [candidate(start=start, end=start+4)]}}
    result = extract_entity_mentions(source, Model())
    mentions = [m for page in result.passages for m in page.mentions]
    assert len(mentions) == 1
    assert mentions[0].citation.start_char == text.index("Sara")
    assert source.native_extraction.resolve_citation(mentions[0].citation) == "Sara"


def test_model_exception_is_sanitized():
    class Model:
        def extract_entities_long(self, *args, **kwargs):
            raise RuntimeError("private source content")
    result = extract_entity_mentions(report_for(["Sara"]), Model())
    assert result.passages[0].status == "failed"
    assert "private source" not in result.model_dump_json()


@pytest.mark.parametrize("threshold", [-1, 2, float("nan"), True])
def test_invalid_threshold(threshold):
    with pytest.raises(ValueError):
        extract_entity_mentions(report_for(["Sara"]), FakeModel({}), threshold=threshold)


def test_description_experiment_preserves_configuration_and_changes_identity():
    source = report_for(["Sara"])
    model = FakeModel({"entities": {"person": [candidate()]}})
    original = extract_entity_mentions(source, model)
    labels = {**LABELS, "project": "A specifically named initiative"}
    changed = extract_entity_mentions(source, model, labels=labels)
    assert changed.labels == labels
    assert changed.passages[0].mentions[0].id != original.passages[0].mentions[0].id
    assert LABELS["project"] != labels["project"]


@pytest.mark.parametrize("labels", [{}, {**LABELS, "project": ""},
    {**LABELS, "project": 1}, {**LABELS, "extra": "Unknown"}])
def test_invalid_label_schema_fails_before_inference(labels):
    with pytest.raises(ValueError):
        extract_entity_mentions(report_for(["Sara"]), FakeModel({}), labels=labels)


@pytest.mark.parametrize("label", ["model", "method", "dataset", "component"])
def test_research_labels_keep_exact_citations(label):
    source = report_for(["BERT"])
    result = extract_entity_mentions(source,
        FakeModel({"entities": {label: [candidate("BERT")]}}), labels=RESEARCH_LABELS)
    mention, = result.passages[0].mentions
    assert result.label_schema_version == "research-v1"
    assert mention.label == label
    assert source.native_extraction.resolve_citation(mention.citation) == "BERT"
    assert EntityReport.model_validate_json(result.model_dump_json()) == result


def test_old_entity_reports_remain_readable():
    result = extract_entity_mentions(report_for(["Sara"]),
        FakeModel({"entities": {"person": [candidate()]}}), labels=CORE_LABELS)
    old = result.model_dump(mode="json")
    old.pop("label_schema_version")
    restored = EntityReport.model_validate(old)
    assert restored.label_schema_version == "legacy"
    assert restored.passages == result.passages


def test_conflicting_types_are_preserved_and_flagged_for_review():
    result = extract_entity_mentions(report_for(["BERT"]), FakeModel({"entities": {
        "model": [candidate("BERT")], "project": [candidate("BERT")],
    }}), labels=RESEARCH_LABELS)
    passage = result.passages[0]
    assert len(passage.mentions) == 2
    assert passage.status == "needs_review"
    assert all("Multiple entity types suggested for this exact span" in mention.warnings
        for mention in passage.mentions)


def test_cli_supports_research_schema(tmp_path, monkeypatch):
    import sys
    from backend.services import entity_mentions

    source = tmp_path / "source.json"
    output = tmp_path / "entities.json"
    source.write_text(report_for(["BERT"]).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(entity_mentions, "load_model",
        lambda: FakeModel({"entities": {"model": [candidate("BERT")]}}))
    monkeypatch.setattr(entity_mentions, "version", lambda name: "2.1.0")
    monkeypatch.setattr(sys, "argv", ["entity_mentions", str(source), "--output", str(output),
        "--schema", "research-v1"])
    entity_mentions.main()
    result = EntityReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert result.label_schema_version == "research-v1"
    assert result.passages[0].mentions[0].label == "model"
