"""Document HTTP contract using mocked Supabase Auth and Data API responses.

These check token propagation; live Supabase policy isolation is a separate test.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app


DOCUMENT_ID = "00000000-0000-0000-0000-000000000010"
ROW = {
    "id": DOCUMENT_ID, "title": "My note", "content": "Original text.\n\nSecond passage.",
    "document_date": "2026-11-13", "collection": "Personal", "synthetic": False,
    "created_at": "2026-11-13T12:00:00Z",
}
HEADERS = {"Authorization": "Bearer token-a"}


@pytest.fixture
def api():
    calls = []
    state = {"storage_status": 200, "auth_status": 200, "rows": [ROW]}

    def handle(request):
        calls.append(request)
        if request.url.path == "/auth/v1/user":
            return httpx.Response(state["auth_status"], json={"id": "00000000-0000-0000-0000-000000000001", "role": "authenticated"})
        assert request.url.path == "/rest/v1/documents"
        if state["storage_status"] != 200:
            return httpx.Response(state["storage_status"], json={"message": "private provider detail"})
        if request.method == "POST":
            return httpx.Response(201, json=[{**ROW, **json.loads(request.content)}])
        return httpx.Response(200, json=state["rows"])

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    try:
        with TestClient(app) as client:
            yield client, calls, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_create_returns_201_and_location_preserving_passages(api):
    client, calls, _ = api
    response = client.post("/documents", headers=HEADERS, json={"title": "  My note  ", "content": ROW["content"], "document_date": "2026-11-13"})
    assert response.status_code == 201
    assert response.headers["location"] == f"/documents/{DOCUMENT_ID}"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["title"] == "My note"
    assert response.json()["content"] == ROW["content"]
    assert len(calls) == 2
    assert calls[-1].headers["authorization"] == "Bearer token-a"
    assert "owner_id" not in json.loads(calls[-1].content)


def test_list_returns_only_metadata_and_forwards_pagination(api):
    client, calls, _ = api
    response = client.get("/documents?limit=5&offset=10", headers=HEADERS)
    assert response.status_code == 200
    assert "content" not in response.json()[0]
    assert "owner_id" not in response.json()[0]
    assert calls[-1].url.params["limit"] == "5"
    assert calls[-1].url.params["offset"] == "10"
    assert "content" not in calls[-1].url.params["select"].split(",")


def test_empty_list_is_success(api):
    client, _, state = api
    state["rows"] = []
    assert client.get("/documents", headers=HEADERS).json() == []


def test_read_returns_full_document_and_filters_id(api):
    client, calls, _ = api
    response = client.get(f"/documents/{DOCUMENT_ID}", headers=HEADERS)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in ROW.items())
    assert response.json()["status"] == "ready"
    assert calls[-1].url.params["id"] == f"eq.{DOCUMENT_ID}"


def test_hidden_or_missing_row_returns_identical_404(api):
    client, _, state = api
    state["rows"] = []
    response = client.get(f"/documents/{DOCUMENT_ID}", headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


@pytest.mark.parametrize("method,path", [("GET", "/documents"), ("POST", "/documents"), ("GET", f"/documents/{DOCUMENT_ID}")])
def test_document_routes_require_auth_without_network(api, method, path):
    client, calls, _ = api
    response = client.request(method, path, json={"title": "Note", "content": "Text"} if method == "POST" else None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert calls == []


def test_rejected_auth_never_reaches_storage(api):
    client, calls, state = api
    state["auth_status"] = 401
    assert client.get("/documents", headers=HEADERS).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {}, {"title": " ", "content": "text"}, {"title": "x", "content": " "},
    {"title": "x", "content": "text", "owner_id": DOCUMENT_ID},
    {"title": "x", "content": "text", "document_date": "not-a-date"},
])
def test_invalid_input_never_reaches_storage(api, payload):
    client, calls, _ = api
    assert client.post("/documents", headers=HEADERS, json=payload).status_code == 422
    assert all(request.url.path == "/auth/v1/user" for request in calls)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
def test_invalid_pagination_returns_422(api, query):
    client, _, _ = api
    assert client.get(f"/documents?{query}", headers=HEADERS).status_code == 422


def test_invalid_document_uuid_returns_422(api):
    client, _, _ = api
    assert client.get("/documents/not-a-uuid", headers=HEADERS).status_code == 422


@pytest.mark.parametrize("method,path", [("GET", "/documents"), ("POST", "/documents"), ("GET", f"/documents/{DOCUMENT_ID}")])
def test_storage_failure_is_sanitized(api, method, path):
    client, _, state = api
    state["storage_status"] = 500
    response = client.request(method, path, headers=HEADERS, json={"title": "Note", "content": "Text"} if method == "POST" else None)
    assert response.status_code == 503
    assert response.json() == {"detail": "Document storage is unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_request_tokens_do_not_leak_between_users(api):
    client, calls, _ = api
    for token in ("token-a", "token-b"):
        assert client.get("/documents", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert [request.headers["authorization"] for request in calls] == ["Bearer token-a", "Bearer token-a", "Bearer token-b", "Bearer token-b"]


def test_openapi_marks_all_document_operations_authenticated(api):
    client, _, _ = api
    paths = client.get("/openapi.json").json()["paths"]
    for path, method in [("/documents", "get"), ("/documents", "post"), ("/documents/{document_id}", "get")]:
        assert paths[path][method]["security"] == [{"HTTPBearer": []}]
