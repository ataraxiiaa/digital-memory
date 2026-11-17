"""Graph rules: confirmed only, entity-level nodes, merges collapse, direction and type corrections."""

from uuid import UUID, uuid4

from backend.schemas.extraction import CitationReference
from backend.schemas.relationships import RelationEnd, Relationship
from backend.services.graph import build_graph

DOC = uuid4()


def end(label, text, run=None, mention=None):
    return RelationEnd(run_id=run or uuid4(), mention_id=mention or uuid4(), label=label, text=text,
        citation=CitationReference(document_id=DOC, extraction_id=uuid4(), page_number=1, region_id=uuid4(),
            start_char=0, end_char=len(text)))


def relation(kind, subject, obj, status="confirmed", **extra):
    return Relationship(id=uuid4(), document_id=DOC, type=kind, subject=subject, object=obj, page_number=1,
        sentence_text="s", sentence_start=0, sentence_end=1, method="m", score=0.9, review_status=status, **extra)


def link(links, entities, item_end, entity_id, name, kind, status="confirmed"):
    links[(str(item_end.run_id), str(item_end.mention_id))] = str(entity_id)
    entities[str(entity_id)] = {"id": str(entity_id), "type": kind, "canonical_name": name, "status": status}


def test_only_confirmed_non_stale_relationships_are_drawn():
    a, b = end("person", "Sara"), end("organization", "Northwind")
    items = [relation("works_at", a, b), relation("works_at", a, b, status="suggested"),
        relation("works_at", a, b, status="rejected"), relation("works_at", a, b, stale=True)]
    graph = build_graph(items, {}, {})
    assert graph.relationship_count == 1 and len(graph.edges) == 1 and len(graph.nodes) == 2


def test_two_mentions_of_one_entity_become_one_node_and_edges_aggregate():
    sara1, sara2 = end("person", "Sara"), end("person", "S. Ahmed")
    org1, org2 = end("organization", "Northwind"), end("organization", "Northwind")
    links, entities = {}, {}
    person, org = uuid4(), uuid4()
    link(links, entities, sara1, person, "Sara Ahmed", "person"); link(links, entities, sara2, person, "Sara Ahmed", "person")
    link(links, entities, org1, org, "Northwind", "organization"); link(links, entities, org2, org, "Northwind", "organization")
    first, second = relation("works_at", sara1, org1), relation("works_at", sara2, org2)
    graph = build_graph([first, second], links, entities)
    assert len(graph.nodes) == 2 and len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.count == 2 and set(edge.evidence_ids) == {first.id, second.id}
    assert {n.name for n in graph.nodes} == {"Sara Ahmed", "Northwind"} and edge.source == str(person)


def test_merged_entities_are_not_used_as_nodes():
    a, b = end("person", "Sara"), end("organization", "Acme")
    links, entities = {}, {}
    link(links, entities, a, uuid4(), "Sara (old)", "person", status="merged")
    graph = build_graph([relation("works_at", a, b)], links, entities)
    assert {n.name for n in graph.nodes} == {"Sara", "Acme"}


def test_swap_and_corrected_type_apply_to_the_edge():
    person, org = end("person", "Imran"), end("organization", "Northwind")
    graph = build_graph([relation("works_at", person, org, corrected_type="member_of")], {}, {})
    assert graph.edges[0].type == "member_of" and graph.edges[0].source.startswith("m:")
    swapped = build_graph([relation("located_in", end("organization", "A"), end("location", "B"), swapped=True)], {}, {})
    assert next(n for n in swapped.nodes if n.id == swapped.edges[0].source).name == "B"


def test_symmetric_edges_have_a_stable_direction_and_self_loops_are_dropped():
    ali, sana = end("person", "Ali"), end("person", "Sana")
    one, two = relation("collaborated_with", ali, sana), relation("collaborated_with", sana, ali)
    graph = build_graph([one, two], {}, {})
    assert len(graph.edges) == 1 and graph.edges[0].count == 2
    links, entities = {}, {}
    same = uuid4()
    link(links, entities, ali, same, "Ali", "person"); link(links, entities, sana, same, "Ali", "person")
    assert build_graph([relation("collaborated_with", ali, sana)], links, entities).edges == []


def test_empty_input_gives_an_empty_graph():
    graph = build_graph([], {}, {})
    assert graph.nodes == [] and graph.edges == [] and graph.relationship_count == 0
