"""Entity run and review routes with a mocked Supabase REST API."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.routes.entities import get_entity_extractor
from backend.routes.processing import get_processing_client_factory
from backend.services.entity_mentions import LABEL_SCHEMAS, extract_entity_mentions
from test_entity_mentions import FakeModel, candidate
from test_source_passages import report_for

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
RUN = "00000000-0000-0000-0000-000000000020"
HEADERS = {"Authorization": "Bearer user-token"}


@pytest.fixture
def api():
    source = report_for(["Sara approved Project Atlas on 3 March 2024."])
    source = source.model_copy(update={"native_extraction": source.native_extraction.model_copy(
        update={"document_id": UUID(DOC)})})
    row = {"id": DOC, "title": "Notes", "content": "", "document_date": None,
        "collection": "Unfiled", "synthetic": True, "created_at": "2026-11-15T00:00:00Z",
        "status": "ready", "storage_path": f"{OWNER}/{DOC}/original.pdf",
        "original_filename": "notes.pdf", "file_size_bytes": 10, "page_count": 1,
        "processing_run_id": RUN, "processing_started_at": None, "processing_error": None}
    state = {"row": row, "hidden": False, "runs": [], "mentions": [], "fail": False, "calls": []}
    model = FakeModel({"entities": {
        "person": [candidate("Sara", 0, 4, .95)],
        "project": [candidate("Project Atlas", 14, 27, .8)],
        "date": [candidate("3 March 2024", 31, 43, .99)]}})

    state["model"] = model

    def handler(request):
        state["calls"].append(request.url.path)
        path = request.url.path
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if path == "/rest/v1/documents":
            return httpx.Response(200, json=[] if state["hidden"] else [row])
        if path == "/rest/v1/document_extractions":
            return httpx.Response(200, json=[{"report": source.model_dump(mode="json")}])
        body = json.loads(request.content) if request.content else None
        if path == "/rest/v1/rpc/start_entity_run":
            if any(r["status"] == "processing" for r in state["runs"]):
                return httpx.Response(400, json={"code": "55000"})
            state["runs"].append({"id": body["p_run_id"], "document_id": DOC, "extraction_id": RUN,
                "status": "processing", "started_at": "2026-11-17T00:00:00Z", "finished_at": None})
            return httpx.Response(200, json=body["p_run_id"])
        if path == "/rest/v1/rpc/finish_entity_run":
            run = next(r for r in state["runs"] if r["id"] == body["p_run_id"])
            run["status"] = body["p_status"]
            if body["p_status"] != "failed":
                run.update(label_schema_version=body["p_meta"]["label_schema_version"],
                    finished_at="2026-11-17T00:01:00Z")
                for m in body["p_mentions"]:
                    state["mentions"].append({**m, "run_id": run["id"], "document_id": DOC,
                        "page_number": m["citation"]["page_number"],
                        "start_char": m["citation"]["start_char"], "end_char": m["citation"]["end_char"],
                        "review_status": "suggested", "reviewed_label": None, "reviewed_at": None})
            return httpx.Response(200, json=True)
        if path == "/rest/v1/entity_runs":
            return httpx.Response(200, json=state["runs"][-1:])
        if path == "/rest/v1/entity_mentions":
            params = request.url.params
            rows = state["mentions"]
            if params["select"] == "*" and "label" in params:
                rows = [m for m in rows if m["label"] == params["label"].removeprefix("eq.")]
            if params["select"] == "*":
                return httpx.Response(200, json=rows[:int(params["limit"])],
                    headers={"content-range": f"0-{max(len(rows) - 1, 0)}/{len(rows)}"})
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/rpc/review_entity_mention":
            mention = next((m for m in state["mentions"] if m["id"] == body["p_mention_id"]), None)
            if mention is None:
                return httpx.Response(400, json={"code": "P0002"})
            mention.update(review_status=body["p_status"], reviewed_label=body["p_label"],
                reviewed_at=None if body["p_status"] == "suggested" else "2026-11-17T00:02:00Z")
            return httpx.Response(200, json=[mention])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    def extractor(report, schema="full-v1"):
        if state["fail"]:
            raise ValueError("sensitive source text")
        state["schema"] = schema
        return extract_entity_mentions(report, model, labels=LABEL_SCHEMAS[schema])

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    app.dependency_overrides[get_entity_extractor] = lambda: extractor
    app.dependency_overrides[get_processing_client_factory] = lambda: lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_empty_before_any_run(api):
    client, _ = api
    data = client.get(f"/documents/{DOC}/entities", headers=HEADERS).json()
    assert data["run"] is None and data["mentions"] == [] and data["counts"]["total"] == 0


def test_run_stores_cited_suggestions_across_all_types(api):
    client, state = api
    response = client.post(f"/documents/{DOC}/entities", headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    page = client.get(f"/documents/{DOC}/entities", headers=HEADERS)
    assert page.headers["cache-control"] == "no-store"
    data = page.json()
    assert data["run"]["status"] == "completed"
    assert data["run"]["label_schema_version"] == "full-v1"
    assert {m["label"] for m in data["mentions"]} == {"person", "project", "date"}
    assert data["counts"] == {"total": 3, "suggested": 3, "accepted": 0, "rejected": 0,
        "by_label": {"person": 1, "project": 1, "date": 1}}
    assert all(m["review_status"] == "suggested" and m["citation"]["document_id"] == DOC
        for m in data["mentions"])


def test_schema_is_selectable_and_validated(api):
    client, state = api
    assert client.post(f"/documents/{DOC}/entities?schema=bogus", headers=HEADERS).status_code == 422
    state["model"].result = {"entities": {"person": [candidate("Sara", 0, 4, .95)]}}
    assert client.post(f"/documents/{DOC}/entities?schema=core-v1", headers=HEADERS).status_code == 202
    assert state["schema"] == "core-v1"
    assert client.get(f"/documents/{DOC}/entities", headers=HEADERS).json()["run"]["label_schema_version"] == "core-v1"


def test_review_accepts_with_corrected_type_and_rejects(api):
    client, _ = api
    client.post(f"/documents/{DOC}/entities", headers=HEADERS)
    mentions = client.get(f"/documents/{DOC}/entities", headers=HEADERS).json()["mentions"]
    project = next(m for m in mentions if m["label"] == "project")
    url = f"/documents/{DOC}/entities/{project['run_id']}/{project['id']}"
    accepted = client.patch(url, json={"status": "accepted", "label": "event"}, headers=HEADERS)
    assert accepted.status_code == 200
    assert accepted.json()["reviewed_label"] == "event" and accepted.json()["label"] == "project"
    rejected = client.patch(url, json={"status": "rejected"}, headers=HEADERS).json()
    assert rejected["review_status"] == "rejected"
    counts = client.get(f"/documents/{DOC}/entities", headers=HEADERS).json()["counts"]
    assert counts["rejected"] == 1 and counts["by_label"] == {"person": 1, "date": 1}


@pytest.mark.parametrize("body", [{"status": "bogus"}, {"status": "rejected", "label": "person"},
    {"status": "accepted", "label": "alien"}, {"status": "accepted", "extra": 1}])
def test_review_rejects_invalid_requests(api, body):
    client, _ = api
    url = f"/documents/{DOC}/entities/{uuid4()}/{uuid4()}"
    assert client.patch(url, json=body, headers=HEADERS).status_code == 422


def test_unknown_mention_is_not_found(api):
    client, _ = api
    url = f"/documents/{DOC}/entities/{uuid4()}/{uuid4()}"
    assert client.patch(url, json={"status": "accepted"}, headers=HEADERS).status_code == 404


def test_failure_is_persisted_without_source_text(api):
    client, state = api
    state["fail"] = True
    response = client.post(f"/documents/{DOC}/entities", headers=HEADERS)
    assert response.status_code == 202 and "sensitive" not in response.text
    assert state["runs"][0]["status"] == "failed" and state["mentions"] == []


def test_duplicate_active_run_is_conflict_and_releases_worker_slot(api):
    client, state = api
    state["runs"].append({"id": str(uuid4()), "document_id": DOC, "extraction_id": RUN,
        "status": "processing", "started_at": "2026-11-17T00:00:00Z", "finished_at": None})
    assert client.post(f"/documents/{DOC}/entities", headers=HEADERS).status_code == 409
    state["runs"].clear()
    assert client.post(f"/documents/{DOC}/entities", headers=HEADERS).status_code == 202


def test_hidden_document_and_missing_auth(api):
    client, state = api
    assert client.get(f"/documents/{DOC}/entities").status_code == 401
    assert client.post(f"/documents/{DOC}/entities").status_code == 401
    state["hidden"] = True
    assert client.get(f"/documents/{DOC}/entities", headers=HEADERS).status_code == 404
    assert client.post(f"/documents/{DOC}/entities", headers=HEADERS).status_code == 404
    assert not any("/rpc/" in call for call in state["calls"])


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "status=maybe",
    "min_score=2", "label=A;B"])
def test_listing_rejects_invalid_filters(api, query):
    client, _ = api
    assert client.get(f"/documents/{DOC}/entities?{query}", headers=HEADERS).status_code == 422
