"""Relationship build, review, and graph routes with a mocked Supabase REST API."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.routes.processing import slots
from backend.routes.relationships import get_relation_extractor
from backend.schemas.extraction import CitationReference
from backend.services.relations import AcceptedMention, build_relation_candidates
from test_relations import FakeModel
from test_source_passages import report_for

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
RUN = "00000000-0000-0000-0000-000000000020"      # extraction
ENTITY_RUN = "00000000-0000-0000-0000-000000000030"
HEADERS = {"Authorization": "Bearer user-token"}
TEXT = "Sara works at Northwind. Omar attended the board retreat."
SPANS = [("person", "Sara"), ("organization", "Northwind"), ("person", "Omar"), ("event", "board retreat")]


def span(value, sentence):
    start = TEXT.index(sentence) + sentence.index(value)
    return {"text": value, "start": sentence.index(value), "end": sentence.index(value) + len(value), "confidence": 0.9}


MODEL_OUTPUT = {
    "Sara works at Northwind.": {"relation_extraction": {"works_at: d": [{"head": span("Sara", "Sara works at Northwind."),
        "tail": span("Northwind", "Sara works at Northwind.")}]}},
    "Omar attended the board retreat.": {"relation_extraction": {"attended": [{"head": span("Omar", "Omar attended the board retreat."),
        "tail": span("board retreat", "Omar attended the board retreat.")}]}},
}


class SentenceModel(FakeModel):
    def extract_relations(self, text, relation_types, **options):
        return MODEL_OUTPUT.get(text, {"relation_extraction": {}})


@pytest.fixture
def api():
    base = report_for([TEXT])
    run = base.native_extraction.model_copy(update={"document_id": UUID(DOC)})
    report = base.model_copy(update={"native_extraction": run})
    region = run.pages[0].regions[0]
    mentions = []
    for label, value in SPANS:
        start = TEXT.index(value)
        mentions.append(AcceptedMention(run_id=UUID(ENTITY_RUN), mention_id=uuid4(), label=label, text=value,
            citation=CitationReference(document_id=UUID(DOC), extraction_id=run.id, page_number=1,
                region_id=region.id, start_char=start, end_char=start + len(value))))
    row = {"id": DOC, "title": "Notes", "content": "", "document_date": None, "collection": "Unfiled", "synthetic": True,
        "created_at": "2026-11-15T00:00:00Z", "status": "ready", "storage_path": f"{OWNER}/{DOC}/original.pdf",
        "original_filename": "n.pdf", "file_size_bytes": 10, "page_count": 1, "processing_run_id": RUN,
        "processing_started_at": None, "processing_error": None}
    state = {"hidden": False, "rows": [], "links": [], "entities": [], "calls": [], "accepted": mentions}

    def handler(request):
        state["calls"].append(request.url.path)
        path, params = request.url.path, request.url.params
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if path == "/rest/v1/documents":
            return httpx.Response(200, json=[] if state["hidden"] else [row])
        if path == "/rest/v1/document_extractions":
            return httpx.Response(200, json=[{"report": report.model_dump(mode="json")}])
        if path == "/rest/v1/entity_runs":
            return httpx.Response(200, json=[{"id": ENTITY_RUN}])
        if path == "/rest/v1/entity_mentions":
            return httpx.Response(200, json=[{"id": str(m.mention_id), "run_id": str(m.run_id), "label": m.label,
                "reviewed_label": None, "text": m.text, "citation": m.citation.model_dump(mode="json")} for m in state["accepted"]])
        if path == "/rest/v1/entity_links":
            if "entity_id" in params:
                wanted = params["entity_id"].removeprefix("eq.")
                return httpx.Response(200, json=[l for l in state["links"] if l["entity_id"] == wanted])
            return httpx.Response(200, json=state["links"])
        if path == "/rest/v1/entities":
            return httpx.Response(200, json=state["entities"])
        if path == "/rest/v1/rpc/replace_relationships":
            body = json.loads(request.content)
            keep = {r["id"]: r for r in state["rows"] if r["review_status"] != "suggested"}
            state["rows"] = list(keep.values())
            for item in body["p_items"]:
                if item["id"] not in keep:
                    state["rows"].append({**item, "review_status": "suggested", "corrected_type": None, "swapped": False,
                        "stale": False, "reviewed_at": None})
            return httpx.Response(200, json={"written": len(body["p_items"]), "removed": 0, "stale": 0})
        if path == "/rest/v1/relationships":
            rows = state["rows"]
            if "id" in params:
                rows = [r for r in rows if r["id"] == params["id"].removeprefix("eq.")]
            if "review_status" in params:
                rows = [r for r in rows if r["review_status"] == params["review_status"].removeprefix("eq.")]
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/rpc/review_relationship":
            body = json.loads(request.content)
            item = next((r for r in state["rows"] if r["id"] == body["p_id"]), None)
            if item is None:
                return httpx.Response(400, json={"code": "P0002"})
            item.update(review_status=body["p_status"], corrected_type=body["p_type"], swapped=bool(body["p_swap"]),
                reviewed_at=None if body["p_status"] == "suggested" else "2026-11-18T00:00:00Z")
            return httpx.Response(200, json=[item])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    model = SentenceModel()

    def extractor(report_, mentions_):
        return build_relation_candidates(report_, mentions_, model)

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    app.dependency_overrides[get_relation_extractor] = lambda: extractor
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def build(client):
    return client.post(f"/documents/{DOC}/relationships/build", headers=HEADERS)


def test_build_stores_cited_suggestions_and_lists_them(api):
    client, state = api
    result = build(client)
    assert result.status_code == 200 and result.json() == {"documents": 1, "candidates": 2, "filtered": 0}
    page = client.get("/relationships", headers=HEADERS)
    assert page.headers["cache-control"] == "no-store"
    data = page.json()
    assert data["total"] == 2
    assert [(r["type"], r["subject"]["text"], r["object"]["text"], r["review_status"]) for r in data["relationships"]] == [
        ("works_at", "Sara", "Northwind", "suggested"), ("attended", "Omar", "board retreat", "suggested")]
    first = data["relationships"][0]
    assert first["sentence_text"] == "Sara works at Northwind." and first["subject"]["citation"]["document_id"] == DOC
    assert client.get("/relationships?type=attended", headers=HEADERS).json()["total"] == 1


def test_review_confirm_reject_and_corrections(api):
    client, state = api
    build(client)
    works_at, attended = client.get("/relationships", headers=HEADERS).json()["relationships"]
    confirmed = client.patch(f"/relationships/{works_at['id']}", json={"status": "confirmed", "type": "member_of"}, headers=HEADERS)
    assert confirmed.status_code == 200 and confirmed.json()["corrected_type"] == "member_of" and confirmed.json()["type"] == "works_at"
    rejected = client.patch(f"/relationships/{attended['id']}", json={"status": "rejected"}, headers=HEADERS).json()
    assert rejected["review_status"] == "rejected"
    visible = client.get("/relationships", headers=HEADERS).json()
    assert [r["id"] for r in visible["relationships"]] == [works_at["id"]]
    assert client.get("/relationships?status=rejected", headers=HEADERS).json()["total"] == 1


@pytest.mark.parametrize("body,code", [
    ({"status": "confirmed", "swap": True}, 422),            # organization -> person does not fit works_at
    ({"status": "confirmed", "type": "located_in"}, 422),    # a person cannot be located_in
    ({"status": "rejected", "type": "member_of"}, 422),      # only confirmed items can be corrected
    ({"status": "bogus"}, 422),
    ({"status": "confirmed", "type": "friends_with"}, 422)])
def test_invalid_review_changes_are_refused(api, body, code):
    client, _ = api
    build(client)
    item = client.get("/relationships", headers=HEADERS).json()["relationships"][0]
    assert client.patch(f"/relationships/{item['id']}", json=body, headers=HEADERS).status_code == code


def test_unknown_relationship_is_not_found(api):
    client, _ = api
    assert client.patch(f"/relationships/{uuid4()}", json={"status": "rejected"}, headers=HEADERS).status_code == 404


def test_graph_shows_only_confirmed_edges_and_collapses_merged_entities(api):
    client, state = api
    build(client)
    works_at, attended = client.get("/relationships", headers=HEADERS).json()["relationships"]
    assert client.get("/graph", headers=HEADERS).json() == {"nodes": [], "edges": [], "relationship_count": 0}
    client.patch(f"/relationships/{works_at['id']}", json={"status": "confirmed"}, headers=HEADERS)
    graph = client.get("/graph", headers=HEADERS).json()
    assert graph["relationship_count"] == 1 and len(graph["edges"]) == 1
    assert {n["name"] for n in graph["nodes"]} == {"Sara", "Northwind"}          # unlinked mentions are their own nodes
    assert graph["edges"][0]["evidence_ids"] == [works_at["id"]]
    # Link Sara to a canonical entity: the node takes the entity's name and id.
    entity = "00000000-0000-0000-0000-0000000000e1"
    state["links"].append({"run_id": ENTITY_RUN, "mention_id": works_at["subject"]["mention_id"], "entity_id": entity})
    state["entities"].append({"id": entity, "type": "person", "canonical_name": "Sara Ahmed", "status": "confirmed"})
    graph = client.get("/graph", headers=HEADERS).json()
    assert {n["name"] for n in graph["nodes"]} == {"Sara Ahmed", "Northwind"}
    assert next(n for n in graph["nodes"] if n["name"] == "Sara Ahmed")["entity_id"] == entity
    # Swapping the direction flips source and target.
    client.patch(f"/relationships/{attended['id']}", json={"status": "confirmed"}, headers=HEADERS)
    assert client.get("/graph", headers=HEADERS).json()["relationship_count"] == 2


def test_entity_filter_uses_links(api):
    client, state = api
    build(client)
    works_at, _ = client.get("/relationships", headers=HEADERS).json()["relationships"]
    entity = "00000000-0000-0000-0000-0000000000e1"
    state["links"].append({"run_id": ENTITY_RUN, "mention_id": works_at["object"]["mention_id"], "entity_id": entity})
    listed = client.get(f"/relationships?entity_id={entity}", headers=HEADERS).json()
    assert [r["id"] for r in listed["relationships"]] == [works_at["id"]]


def test_hidden_document_and_missing_auth(api):
    client, state = api
    assert client.post(f"/documents/{DOC}/relationships/build").status_code == 401
    assert client.get("/relationships").status_code == 401
    assert client.get("/graph").status_code == 401
    state["hidden"] = True
    assert build(client).status_code == 404
    assert not any("rpc/replace" in call for call in state["calls"])


def test_busy_worker_slot_is_a_conflict_and_is_released(api):
    client, _ = api
    assert slots.acquire(blocking=False)
    try:
        assert build(client).status_code == 409
    finally:
        slots.release()
    assert build(client).status_code == 200


def test_build_all_covers_finished_documents(api):
    client, _ = api
    result = client.post("/relationships/build", headers=HEADERS)
    assert result.status_code == 200 and result.json()["documents"] == 1 and result.json()["candidates"] == 2


def test_rebuild_keeps_reviewed_decisions(api):
    client, _ = api
    build(client)
    item = client.get("/relationships", headers=HEADERS).json()["relationships"][0]
    client.patch(f"/relationships/{item['id']}", json={"status": "confirmed"}, headers=HEADERS)
    build(client)
    again = client.get("/relationships?status=confirmed", headers=HEADERS).json()
    assert [r["id"] for r in again["relationships"]] == [item["id"]]


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "status=maybe", "type=friends_with", "entity_id=x"])
def test_listing_rejects_invalid_filters(api, query):
    client, _ = api
    assert client.get(f"/relationships?{query}", headers=HEADERS).status_code == 422
