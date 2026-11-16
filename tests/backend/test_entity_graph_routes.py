"""Canonical entity routes against a mocked Supabase REST API."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
RUN = "00000000-0000-0000-0000-000000000020"
E1 = "00000000-0000-0000-0000-0000000000a1"
E2 = "00000000-0000-0000-0000-0000000000a2"
SUG = "00000000-0000-0000-0000-0000000000b1"
HEADERS = {"Authorization": "Bearer user-token"}


def mention_row(mid, text, label="person", start=0):
    return {"id": mid, "run_id": RUN, "document_id": DOC, "text": text, "label": label, "reviewed_label": None}


def entity_row(eid, name, status="suggested", type_="person"):
    return {"id": eid, "type": type_, "canonical_name": name, "status": status, "created_at": "2026-11-18T00:00:00Z"}


def link_row(eid, mid, text, doc=DOC):
    return {"entity_id": eid, "run_id": RUN, "mention_id": mid, "document_id": doc,
        "entity_mentions": {"text": text, "page_number": 1, "citation": {"document_id": DOC,
            "extraction_id": str(uuid4()), "page_number": 1, "region_id": str(uuid4()), "start_char": 0, "end_char": len(text)}}}


@pytest.fixture
def api():
    m1, m2, m3 = (str(uuid4()) for _ in range(3))
    state = {"calls": [], "rpc_error": None, "applied": None, "hidden": False,
        "links": [link_row(E1, m1, "Larry Page"), link_row(E1, m2, "LARRY PAGE"), link_row(E2, m3, "L. Page")],
        "entities": [entity_row(E1, "Larry Page"), entity_row(E2, "L. Page")], "m": (m1, m2, m3)}

    def handler(request):
        path, params = request.url.path, request.url.params
        state["calls"].append((request.method, path, dict(params)))
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        body = json.loads(request.content) if request.content else None
        if path.startswith("/rest/v1/rpc/"):
            name = path.rsplit("/", 1)[1]
            if state["rpc_error"]:
                return httpx.Response(400, json={"code": state["rpc_error"]})
            if name == "refresh_entity_links":
                return httpx.Response(200, json={"relinked": 1, "dropped_links": 0, "removed_empty_entities": 0})
            if name == "apply_entity_resolution":
                state["applied"] = body
                return httpx.Response(200, json={"new_entities": len(body["p_entities"]), "new_links": len(body["p_links"]),
                    "pending_suggestions": len(body["p_suggestions"])})
            if name == "merge_entities":
                return httpx.Response(200, json=str(uuid4()))
            if name == "split_mention":
                state["entities"].append(entity_row(body["p_new_id"], "Split", status="confirmed"))
                return httpx.Response(200, json=body["p_new_id"])
            return httpx.Response(200, json=None)
        if path == "/rest/v1/entity_runs":
            return httpx.Response(200, json=[{"id": RUN, "document_id": DOC, "started_at": "2026-11-18T00:00:00Z", "status": "completed"}])
        if path == "/rest/v1/entity_mentions":
            return httpx.Response(200, json=[mention_row(state["m"][0], "Larry Page"), mention_row(state["m"][1], "L. Page")])
        if path == "/rest/v1/entity_links":
            wanted = params.get("entity_id")
            rows = state["links"]
            if wanted:
                ids = wanted.removeprefix("in.(").removesuffix(")").split(",")
                rows = [r for r in rows if r["entity_id"] in ids]
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/entities":
            rows = state["entities"]
            if params.get("id", "").startswith("eq."):
                rows = [r for r in rows if r["id"] == params["id"][3:]]
            elif params.get("id", "").startswith("in."):
                ids = params["id"].removeprefix("in.(").removesuffix(")").split(",")
                rows = [r for r in rows if r["id"] in ids]
            if state["hidden"]:
                rows = []
            return httpx.Response(200, json=rows, headers={"content-range": f"0-{max(len(rows) - 1, 0)}/{len(rows)}"})
        if path == "/rest/v1/merge_suggestions":
            row = {"id": SUG, "entity_a": E1, "entity_b": E2, "kind": "initial", "score": 0.85, "ambiguous": False,
                "reason": "“L. Page” may be “Larry Page”", "status": "pending"}
            return httpx.Response(200, json=[row], headers={"content-range": "0-0/1"})
        if path == "/rest/v1/entity_merges":
            return httpx.Response(200, json=[])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_resolve_groups_exact_mentions_and_suggests_but_never_merges(api):
    client, state = api
    state["links"].clear()  # the two accepted mentions served by the mock are not linked yet
    state["entities"].clear()
    response = client.post("/entities/resolve", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    plan = state["applied"]
    assert sorted(e["canonical_name"] for e in plan["p_entities"]) == ["L. Page", "Larry Page"]
    assert len(plan["p_links"]) == 2
    (suggestion,) = plan["p_suggestions"]
    assert suggestion["kind"] == "initial" and suggestion["entity_a"] < suggestion["entity_b"]
    assert response.json()["new_entities"] == 2 and response.json()["relinked"] == 1
    assert not any("merge_entities" in path for _, path, _ in state["calls"])


def test_resolve_requires_authentication(api):
    client, state = api
    assert client.post("/entities/resolve").status_code == 401
    assert not state["calls"]


def test_list_entities_reports_counts_aliases_and_documents(api):
    client, _ = api
    data = client.get("/entities", headers=HEADERS).json()
    assert data["total"] == 2
    larry = next(e for e in data["entities"] if e["canonical_name"] == "Larry Page")
    assert larry["mention_count"] == 2 and larry["document_count"] == 1
    assert larry["aliases"] == ["Larry Page", "LARRY PAGE"]


def test_list_filters_are_validated_and_sanitized(api):
    client, state = api
    assert client.get("/entities?type=alien", headers=HEADERS).status_code == 422
    assert client.get("/entities?limit=0", headers=HEADERS).status_code == 422
    client.get("/entities?type=person&q=pa*ge),(", headers=HEADERS)
    params = next(p for m, path, p in state["calls"] if path == "/rest/v1/entities")
    assert params["type"] == "eq.person" and params["canonical_name"] == "ilike.*page*"
    assert params["status"] == "neq.merged"


def test_entity_detail_lists_cited_mentions(api):
    client, _ = api
    data = client.get(f"/entities/{E1}", headers=HEADERS).json()
    assert [m["text"] for m in data["mentions"]] == ["Larry Page", "LARRY PAGE"]
    assert data["mentions"][0]["citation"]["document_id"] == DOC
    assert client.get(f"/entities/{uuid4()}", headers=HEADERS).status_code == 404
    assert client.get("/entities/not-a-uuid", headers=HEADERS).status_code == 422


def test_suggestions_include_reason_and_source_evidence(api):
    client, _ = api
    data = client.get("/entities/suggestions", headers=HEADERS).json()
    (item,) = data["suggestions"]
    assert item["kind"] == "initial" and "may be" in item["reason"]
    assert {item["a"]["canonical_name"], item["b"]["canonical_name"]} == {"Larry Page", "L. Page"}
    assert item["a"]["evidence"] and item["a"]["evidence"][0]["citation"]["document_id"] == DOC
    assert client.get("/entities/suggestions?status=bogus", headers=HEADERS).status_code == 422


def test_confirming_a_suggestion_keeps_the_entity_with_more_mentions(api):
    client, state = api
    response = client.post(f"/entities/suggestions/{SUG}/confirm", headers=HEADERS)
    assert response.status_code == 200
    assert any(c[1].endswith("merge_entities") for c in state["calls"])
    assert response.json()["entity"]["id"] == E1  # E1 has two mentions, E2 one


def test_confirm_can_choose_the_survivor_but_only_from_the_pair(api):
    client, _ = api
    assert client.post(f"/entities/suggestions/{SUG}/confirm", json={"keep_id": E2}, headers=HEADERS).json()["entity"]["id"] == E2
    assert client.post(f"/entities/suggestions/{SUG}/confirm", json={"keep_id": str(uuid4())}, headers=HEADERS).status_code == 422


def test_manual_merge_rename_confirm_link_split_dismiss_undo(api):
    client, state = api
    assert client.post("/entities/merge", json={"keep_id": E1, "absorb_id": E2}, headers=HEADERS).status_code == 200
    assert client.patch(f"/entities/{E1}", json={"name": "Larry E. Page"}, headers=HEADERS).status_code == 200
    assert client.patch(f"/entities/{E1}", json={"name": ""}, headers=HEADERS).status_code == 422
    assert client.post(f"/entities/{E1}/confirm", headers=HEADERS).status_code == 200
    target = {"run_id": RUN, "mention_id": state["m"][2]}
    assert client.post(f"/entities/{E1}/link", json=target, headers=HEADERS).status_code == 200
    # Unlinking a mention splits it into a new entity; the new entity is returned.
    split = client.post("/entities/mentions/split", json=target, headers=HEADERS)
    assert split.status_code == 201 and split.json()["entity"]["status"] == "confirmed"
    assert client.post(f"/entities/suggestions/{SUG}/dismiss", headers=HEADERS).status_code == 204
    assert client.post(f"/entities/merges/{uuid4()}/undo", headers=HEADERS).status_code == 204
    paths = [path for _, path, _ in state["calls"]]
    for name in ("merge_entities", "rename_entity", "confirm_entity", "link_mention", "split_mention",
                 "dismiss_merge_suggestion", "undo_merge"):
        assert f"/rest/v1/rpc/{name}" in paths


def test_invalid_bodies_are_rejected_before_storage(api):
    client, state = api
    assert client.post("/entities/merge", json={"keep_id": E1}, headers=HEADERS).status_code == 422
    assert client.post("/entities/merge", json={"keep_id": E1, "absorb_id": E2, "x": 1}, headers=HEADERS).status_code == 422
    assert client.post("/entities/mentions/split", json={"run_id": "x"}, headers=HEADERS).status_code == 422
    assert not any("/rpc/" in path for _, path, _ in state["calls"])


@pytest.mark.parametrize("code,status", [("P0002", 404), ("22023", 422), ("XX000", 503)])
def test_database_errors_map_to_safe_statuses(api, code, status):
    client, state = api
    state["rpc_error"] = code
    response = client.post("/entities/merge", json={"keep_id": E1, "absorb_id": E2}, headers=HEADERS)
    assert response.status_code == status and "XX000" not in response.text
