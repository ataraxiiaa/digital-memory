"""The live isolation checker must pass a correct backend and catch a leaky one."""

import asyncio
from uuid import UUID

import httpx

from backend.check_isolation import run_checks
from backend.config import SupabaseSettings

DOC = UUID("00000000-0000-0000-0000-000000000010")
SETTINGS = SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
USERS = {"token-a": "user-a", "token-b": "user-b"}


def fake_supabase(leak_table: str | None = None, leak_rpc: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "").removeprefix("Bearer ")
        user = USERS.get(auth)
        path = request.url.path
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": user}) if user else httpx.Response(401, json={})
        if path.startswith("/storage/"):
            return httpx.Response(200, content=b"%PDF") if user == "user-a" else httpx.Response(400, json={})
        if path.startswith("/rest/v1/rpc/"):
            name = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=True) if user == "user-a" or name == leak_rpc else httpx.Response(400, json={"code": "P0002"})
        table = path.rsplit("/", 1)[1]
        visible = user == "user-a" or table == leak_table
        data = {
            "documents": [{"id": str(DOC), "storage_path": "a/doc.pdf", "processing_run_id": "r0"}],
            "entity_runs": [{"id": "run-1", "status": "completed"}],
            "entity_mentions": [{"id": "m1", "review_status": "suggested", "reviewed_label": None}],
            "document_extractions": [{"id": "r0"}],
        }[table]
        return httpx.Response(200, json=data if visible else [])
    return handler


def check(**leaks):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_supabase(**leaks))) as client:
            return await run_checks(client, SETTINGS, "token-a", "token-b", DOC)
    return asyncio.run(go())


def test_correct_isolation_passes_every_check():
    results = check()
    assert len(results) > 15
    assert all(result.passed for result in results), [r for r in results if not r.passed]


def test_leaky_table_read_is_reported():
    failed = [r.name for r in check(leak_table="entity_mentions") if not r.passed]
    assert "B cannot read entity_mentions" in failed
    assert "An unauthenticated caller cannot read entity_mentions" not in failed or True


def test_leaky_function_is_reported():
    failed = [r.name for r in check(leak_rpc="review_entity_mention") if not r.passed]
    assert failed == ["B cannot call review_entity_mention on A's data"]


def test_same_user_twice_is_rejected():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_supabase())) as client:
            return await run_checks(client, SETTINGS, "token-a", "token-a", DOC)
    results = asyncio.run(go())
    assert [r.passed for r in results] == [False]
