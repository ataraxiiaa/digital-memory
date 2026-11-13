"""Offline adapter tests. Live Supabase RLS is a separate integration check."""

import asyncio
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from backend.schemas.documents import NewDocument
from backend.storage.documents import StorageUnavailable, SupabaseDocumentStore


ROW = {
    "id": "00000000-0000-0000-0000-000000000001", "title": "Sample",
    "content": "An original passage.", "document_date": "2026-11-13",
    "collection": "Studio", "synthetic": True,
    "created_at": "2026-11-13T00:00:00Z",
}


def run_request(handler, operation):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            store = SupabaseDocumentStore(client, url="https://example.supabase.co", publishable_key="sb_publishable_test", access_token="user-token")
            return await operation(store)
    return asyncio.run(run())


def test_create_sends_user_credentials_and_preserves_long_unicode_text():
    import json
    content = "Notes — یادداشت\n\n" * 30_000

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/documents"
        assert request.headers["authorization"] == "Bearer user-token"
        assert request.headers["apikey"] == "sb_publishable_test"
        assert request.headers["prefer"] == "return=representation"
        payload = json.loads(request.content)
        assert payload["content"] == content
        assert "owner_id" not in payload
        return httpx.Response(201, json=[{**ROW, "content": content}])

    result = run_request(handler, lambda store: store.create(NewDocument(title="Sample", content=content)))
    assert result.content == content


def test_list_paginates_without_fetching_full_content():
    def handler(request):
        assert "content" not in request.url.params["select"].split(",")
        assert request.url.params["limit"] == "10"
        assert request.url.params["offset"] == "20"
        assert request.url.params["order"] == "created_at.desc,id.desc"
        return httpx.Response(200, json=[{key: value for key, value in ROW.items() if key != "content"}])
    result = run_request(handler, lambda store: store.list(limit=10, offset=20))
    assert result[0].id == UUID(ROW["id"])


def test_get_returns_document_or_none_for_hidden_or_missing_row():
    result = run_request(lambda request: httpx.Response(200, json=[ROW]), lambda store: store.get(UUID(ROW["id"])))
    assert result.content == ROW["content"]
    assert run_request(lambda request: httpx.Response(200, json=[]), lambda store: store.get(UUID(ROW["id"]))) is None


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_storage_failures_do_not_expose_upstream_details(status):
    with pytest.raises(StorageUnavailable) as caught:
        run_request(lambda request: httpx.Response(status, text="sensitive upstream detail"), lambda store: store.list())
    assert "sensitive" not in str(caught.value)


def test_network_failure_is_sanitized():
    def handler(request):
        raise httpx.ConnectError("sensitive URL", request=request)
    with pytest.raises(StorageUnavailable, match="unavailable"):
        run_request(handler, lambda store: store.list())


@pytest.mark.parametrize("payload", [{}, [{"id": "invalid"}]])
def test_invalid_responses_are_handled(payload):
    with pytest.raises(StorageUnavailable, match="Invalid"):
        run_request(lambda request: httpx.Response(200, json=payload), lambda store: store.list())


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (10, -1)])
def test_invalid_pagination_is_rejected_before_network(limit, offset):
    def handler(request):
        pytest.fail("Invalid input should not reach Supabase")
    with pytest.raises(ValueError):
        run_request(handler, lambda store: store.list(limit=limit, offset=offset))


@pytest.mark.parametrize("data", [
    {"title": " ", "content": "Text"},
    {"title": "Title", "content": " "},
    {"title": "Title", "content": "a\x00b"},
    {"title": "Title", "content": "Text", "owner_id": ROW["id"]},
])
def test_invalid_content_and_owner_override_are_rejected(data):
    with pytest.raises(ValidationError):
        NewDocument(**data)


def test_secret_key_is_rejected():
    with httpx.Client() as unused:
        with pytest.raises(ValueError, match="publishable"):
            SupabaseDocumentStore(unused, url="https://example.supabase.co", publishable_key="sb_secret_test", access_token="token")
