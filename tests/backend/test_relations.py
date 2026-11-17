"""Relation candidates: span matching, type validation, guards, and fixture integrity."""

import pytest

from backend.services.relations import guard_reason, parse_relations, valid_pair
from relations_eval_support import expected, gold_mentions, load_cases, run_split
from backend.services.relations import build_relation_candidates


def rel(kind, head, tail, text, conf=0.9):
    def span(value):
        start = text.index(value)
        return {"text": value, "start": start, "end": start + len(value), "confidence": conf}
    return {"relation_extraction": {f"{kind}: description": [{"head": span(head), "tail": span(tail)}]}}


class FakeModel:
    def __init__(self, result=None, error=False):
        self.result, self.error, self.calls = result or {"relation_extraction": {}}, error, []

    def extract_relations(self, text, relation_types, **options):
        self.calls.append((text, options))
        if self.error:
            raise RuntimeError("provider error quoting source text")
        return self.result


def case(text, entities):
    return [{"split": "dev", "text": text, "entities": entities, "relations": []}]


def build(text, entities, result, **kwargs):
    return run_split(case(text, entities), FakeModel(result), **kwargs)


def test_exact_spans_stable_ids_and_citations():
    text = "Sara works at Northwind."
    entities = {"person": ["Sara"], "organization": ["Northwind"]}
    report, mentions = gold_mentions(case(text, entities))
    model = FakeModel(rel("works_at", "Sara", "Northwind", text))
    first = build_relation_candidates(report, mentions, model)
    candidate, = first.candidates
    assert (candidate.type, candidate.subject.text, candidate.object.text) == ("works_at", "Sara", "Northwind")
    assert candidate.sentence_text == text and candidate.method == "gliner2-relations"
    assert report.native_extraction.resolve_citation(candidate.subject.citation) == "Sara"
    assert report.native_extraction.resolve_citation(candidate.object.citation) == "Northwind"
    # The same source yields the same id; the id comes from source spans, not mention ids.
    again = build_relation_candidates(report, mentions, model)
    assert again.candidates[0].id == candidate.id


@pytest.mark.parametrize("kind,head,tail", [
    ("works_at", "Northwind", "Sara"),          # wrong direction
    ("located_in", "Sara", "Northwind"),        # wrong types
    ("attended", "Sara", "Northwind"),
])
def test_wrong_direction_or_types_are_rejected_and_counted(kind, head, tail):
    text = "Sara works at Northwind."
    result = build(text, {"person": ["Sara"], "organization": ["Northwind"]}, rel(kind, head, tail, text))
    assert result.candidates == () and result.counts.invalid_types == 1


def test_spans_that_are_not_accepted_mentions_are_ignored():
    text = "Sara works at Northwind Labs."
    result = build(text, {"person": ["Sara"], "organization": ["Northwind"]}, rel("works_at", "Sara", "Northwind Labs", text))
    assert result.candidates == () and result.counts.unmatched_spans == 1


@pytest.mark.parametrize("text,entities,kind,subject,obj,counter", [
    ("Brian did not join Acme Corp.", {"person": ["Brian"], "organization": ["Acme Corp"]}, "works_at", "Brian", "Acme Corp", "filtered_negated"),
    ("Maya may work at Zenith Bank.", {"person": ["Maya"], "organization": ["Zenith Bank"]}, "works_at", "Maya", "Zenith Bank", "filtered_hedged"),
    ("Zubair left Acme Corp last year.", {"person": ["Zubair"], "organization": ["Acme Corp"]}, "works_at", "Zubair", "Acme Corp", "filtered_ended"),
    ("Sara and Brian met in Berlin.", {"person": ["Sara", "Brian"]}, "collaborated_with", "Sara", "Brian", "filtered_cue"),
    ("Google was founded in California by Larry Page.", {"organization": ["Google"], "location": ["California"]}, "located_in", "Google", "California", "filtered_cue"),
])
def test_guards_drop_unusable_statements(text, entities, kind, subject, obj, counter):
    result = build(text, entities, rel(kind, subject, obj, text))
    assert result.candidates == ()
    assert getattr(result.counts, counter) == 1


def test_negation_elsewhere_in_the_sentence_does_not_block_a_valid_relation():
    text = "Sara works at Northwind, but Brian did not join Acme Corp."
    entities = {"person": ["Sara", "Brian"], "organization": ["Northwind", "Acme Corp"]}
    assert len(build(text, entities, rel("works_at", "Sara", "Northwind", text)).candidates) == 1


def test_symmetric_relations_put_the_earlier_mention_first():
    text = "Ali and Sana collaborated on a report."
    result = build(text, {"person": ["Ali", "Sana"]}, rel("collaborated_with", "Sana", "Ali", text))
    candidate, = result.candidates
    assert (candidate.subject.text, candidate.object.text) == ("Ali", "Sana")


def test_part_of_only_pairs_compatible_types():
    text = "The encoder is part of Northwind."
    result = build(text, {"component": ["encoder"], "organization": ["Northwind"]}, rel("part_of", "encoder", "Northwind", text))
    assert result.candidates == () and result.counts.invalid_types == 1


def test_model_failure_is_counted_without_leaking_provider_text():
    text = "Sara works at Northwind."
    cases = case(text, {"person": ["Sara"], "organization": ["Northwind"]})
    result = run_split(cases, FakeModel(error=True))
    assert result.candidates == () and result.counts.failed_sentences == 1
    assert "provider" not in result.model_dump_json()


def test_sentences_with_fewer_than_two_mentions_never_reach_the_model():
    model = FakeModel()
    run_split(case("Sara works somewhere.", {"person": ["Sara"]}), model)
    assert model.calls == []


def test_each_sentence_is_checked_separately():
    text = "Sara works at Northwind. Brian works at Acme Corp."
    entities = {"person": ["Sara", "Brian"], "organization": ["Northwind", "Acme Corp"]}
    model = FakeModel()
    run_split(case(text, entities), model)
    assert [call[0] for call in model.calls] == ["Sara works at Northwind.", "Brian works at Acme Corp."]
    # A relation the model invents across sentences cannot match mentions of another sentence.
    cross = rel("works_at", "Sara", "Acme Corp", text)
    first_sentence = build(text, entities, {"relation_extraction": {"works_at": cross["relation_extraction"]["works_at: description"]}})
    assert first_sentence.candidates == ()


def test_parse_relations_skips_unknown_types_and_malformed_items():
    raw = {"relation_extraction": {"invented": [{"head": {}, "tail": {}}], "works_at": ["x", {"head": {}}],
        "attended: d": [{"head": {"start": 0}, "tail": {"start": 1}}]}}
    assert [item[0] for item in parse_relations(raw)] == ["attended"]
    assert parse_relations({}) == [] and parse_relations(None) == []


def test_guard_reason_and_pair_validation_helpers():
    assert guard_reason(" was not at ") == "negated" and guard_reason(" might join ") == "hedged"
    assert guard_reason(" works at ") is None
    _, mentions = gold_mentions(case("Sara works at Northwind.", {"person": ["Sara"], "organization": ["Northwind"]}))
    person, org = mentions
    assert valid_pair("works_at", person, org) and not valid_pair("works_at", org, person)


def test_fixture_integrity_and_split():
    cases = load_cases()
    assert len(cases) == 40
    assert [c["split"] for c in cases] == ["dev"] * 20 + ["evaluation"] * 20
    for number, item in enumerate(cases, 1):
        names = {name for names in item["entities"].values() for name in names}
        for kind, subject, obj in item["relations"]:
            assert subject in names and obj in names and subject != obj, (number, kind)
        assert all(name in item["text"] for name in names)
    assert len(expected(cases)) == 37
    # The held-out half contains negatives of every authored kind.
    held_out = load_cases("evaluation")
    assert sum(not c["relations"] for c in held_out) >= 5
