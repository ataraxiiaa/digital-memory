"""Deterministic identity matching, including the Larry Page / L. Page cases."""

import json
from itertools import count
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.services.entity_resolution import (
    EntityState, MentionRef, entity_key, match_names, parse_person, resolve, suggest_merges,
)

PAIRS = json.loads(Path("tests/fixtures/name_variant_pairs.json").read_text(encoding="utf-8"))["pairs"]


def kind(entity_type, a, b):
    found = match_names(entity_type, a, b)
    return found.kind if found else None


@pytest.mark.parametrize("a,b,expected", [
    ("Larry Page", "L. Page", "initial"), ("Larry Page", "L Page", "initial"),
    ("Larry Page", "Page, Larry", "exact"), ("Larry Page", "Dr. Larry Page", "exact"),
    ("L. Page", "L Page", "exact"), ("Larry Page", "Page", "surname"),
    ("Larry Page", "Lisa Page", None), ("Larry Page", "Lee Page", None), ("Larry Page", "Larry Paige", None),
    ("Łukasz Kaiser", "L. Kaiser", "initial"), ("Smith et al.", "Smith", "exact"),
    ("Mary-Jane Watson", "Mary Watson", None), ("Ludwig van Beethoven", "L. van Beethoven", "initial"),
])
def test_person_matching(a, b, expected):
    assert kind("person", a, b) == expected
    assert kind("person", b, a) == expected  # symmetric


def test_person_parsing_handles_order_titles_and_particles():
    assert parse_person("Page, Larry").key == "larry page"
    assert parse_person("Prof. Sara Khan Jr.").key == "sara khan"
    assert parse_person("Ahmed bin Rashid").last == "bin rashid"
    assert parse_person("") is None and parse_person("!!!") is None


def test_every_match_carries_a_reason_and_a_score_below_one_unless_exact():
    for pair in PAIRS:
        found = match_names(pair["type"], pair["a"], pair["b"])
        if found:
            assert found.reason and 0 < found.score <= 1
            assert (found.score == 1) == (found.kind == "exact")


def test_organization_suffixes_and_related_names():
    assert kind("organization", "Acme Inc.", "ACME") == "exact"
    assert kind("organization", "Google", "Google Brain") == "related"
    assert match_names("organization", "Google", "Google Brain").score < 0.5
    assert kind("organization", "Zenith Bank", "Zenith Insurance") is None


def test_other_types_only_match_exactly_and_never_across_types():
    assert kind("date", "3 March 2024", "3 march 2024") == "exact"
    assert kind("location", "Lahore", "Lahore Fort") is None
    assert entity_key("method", "Self-Attention") == entity_key("method", "self attention")
    # Keys are only compared within a type, so equal text never links a person to an organization.
    plan = resolve([ref("person", "Mercury"), ref("organization", "Mercury"), ref("location", "Mercury")], [])
    assert sorted(e.type for e in plan.new_entities) == ["location", "organization", "person"]


def ref(entity_type, text, run=None):
    return MentionRef(run or uuid4(), uuid4(), uuid4(), entity_type, text)


def sequential_ids():
    counter = count(1)
    return lambda: UUID(int=next(counter))


def test_identical_normalized_mentions_group_into_one_new_entity():
    plan = resolve([ref("person", "Larry Page"), ref("person", "larry  page"), ref("person", "Dr. Larry Page"),
                    ref("organization", "Larry Page")], [], new_id=sequential_ids())
    assert sorted((e.type, e.canonical_name) for e in plan.new_entities) == [
        ("organization", "Larry Page"), ("person", "Larry Page")]
    assert len(plan.links) == 4
    assert plan.suggestions == []  # a person and an organization never get a suggestion


def test_larry_page_and_l_page_are_suggested_for_merging_not_merged():
    plan = resolve([ref("person", "Larry Page"), ref("person", "L. Page")], [], new_id=sequential_ids())
    assert len(plan.new_entities) == 2  # nothing merged silently
    (suggestion,) = plan.suggestions
    assert suggestion.kind == "initial" and not suggestion.ambiguous
    assert "initial" in suggestion.reason and suggestion.entity_a < suggestion.entity_b


def test_initial_matching_two_different_people_is_flagged_ambiguous():
    plan = resolve([ref("person", "Larry Page"), ref("person", "Lisa Page"), ref("person", "L. Page")],
                   [], new_id=sequential_ids())
    kinds = {(s.kind, s.ambiguous) for s in plan.suggestions}
    assert kinds == {("initial", True)}
    assert len(plan.suggestions) == 2  # "L. Page" against each; Larry vs Lisa is never suggested
    assert all("Ambiguous" in s.reason for s in plan.suggestions)
    names = " ".join(s.reason for s in plan.suggestions)
    assert "Larry Page" in names and "Lisa Page" in names


def test_surname_only_is_weak_and_never_links_to_an_existing_entity():
    existing = EntityState(uuid4(), "person", "Larry Page", ("Larry Page",))
    plan = resolve([ref("person", "Page")], [existing], new_id=sequential_ids())
    assert plan.links[0].entity_id != existing.id  # a new entity, not a silent link
    (suggestion,) = plan.suggestions
    assert suggestion.kind == "surname" and suggestion.score < 0.5


def test_exact_alias_links_to_existing_entity_but_two_candidates_are_skipped():
    one = EntityState(uuid4(), "person", "Larry Page", ("Larry Page",))
    plan = resolve([ref("person", "LARRY PAGE")], [one])
    assert [l.entity_id for l in plan.links] == [one.id] and plan.new_entities == []
    two = EntityState(uuid4(), "person", "Larry Page", ("Larry Page",))
    plan = resolve([ref("person", "Larry Page")], [one, two])
    assert plan.links == [] and plan.new_entities == [] and plan.skipped_ambiguous == 1


def test_resolution_is_deterministic_and_idempotent_for_stable_ids():
    mentions = [ref("person", "Larry Page"), ref("person", "L. Page"), ref("organization", "Acme Inc.")]
    first = resolve(mentions, [], new_id=sequential_ids())
    second = resolve(mentions, [], new_id=sequential_ids())
    assert first == second
    # Re-running with everything already linked must reproduce the same suggestions and change nothing else.
    existing = [EntityState(e.id, e.type, e.canonical_name, (e.canonical_name,)) for e in first.new_entities]
    again = resolve([], existing)
    assert again.suggestions == first.suggestions and not again.new_entities and not again.links


def test_location_variants_do_not_generate_fuzzy_suggestions():
    plan = resolve([ref("location", "Lahore"), ref("location", "Lahore Fort")], [], new_id=sequential_ids())
    assert len(plan.new_entities) == 2 and plan.suggestions == []


def test_fixture_hard_requirements_hold():
    for pair in PAIRS:
        found = match_names(pair["type"], pair["a"], pair["b"])
        strong = found is not None and found.kind in ("exact", "initial")
        if pair["gold"] == "different" and pair["type"] == "person":
            assert not strong, pair
        if pair["gold"] == "same" and pair["type"] != "person":
            assert found is not None, pair
