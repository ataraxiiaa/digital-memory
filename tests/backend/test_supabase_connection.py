"""Configuration and read-only connection diagnostics with no cloud access."""

import asyncio

import httpx
import pytest

from backend.check_supabase import ConnectionCheckError, check_connection, check_documents
from backend.config import ConfigurationError, SupabaseSettings, load_supabase_settings
from backend.storage.connection import open_document_store


SETTINGS = SupabaseSettings("https://example.supabase.co", "sb_publishable_test")


@pytest.fixture(autouse=True)
def clear_config(monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_loads_root_style_env_and_environment_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('SUPABASE_URL=https://example.supabase.co\nSUPABASE_PUBLISHABLE_KEY=sb_publishable_file\n', encoding="utf-8")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_override")
    settings = load_supabase_settings(env)
    assert settings.publishable_key == "sb_publishable_override"
    assert "sb_publishable_override" not in repr(settings)


def test_missing_config_explains_variable_names_without_values(tmp_path):
    with pytest.raises(ConfigurationError, match="SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY"):
        load_supabase_settings(tmp_path / "absent")


@pytest.mark.parametrize("url,key", [
    ("http://example.supabase.co", "sb_publishable_test"),
    ("https://user:password@example.supabase.co", "sb_publishable_test"),
    ("https://example.supabase.co/path", "sb_publishable_test"),
    ("https://example.supabase.co?token=private", "sb_publishable_test"),
    ("https://example.supabase.co", "sb_secret_private"),
    ("https://your-project-ref.supabase.co", "sb_publishable_replace_me"),
])
def test_rejects_invalid_config_without_disclosing_it(url, key):
    with pytest.raises(ConfigurationError) as caught:
        SupabaseSettings(url, key)
    assert key not in str(caught.value)
    assert "password" not in str(caught.value)


def run_check(handler, *, documents=False):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if documents:
                return await check_documents(client, SETTINGS, "user-token")
            return await check_connection(client, SETTINGS)
    return asyncio.run(run())


def test_connectivity_check_sends_only_read_request_and_key():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/auth/v1/health"
        assert request.headers["apikey"] == SETTINGS.publishable_key
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"name": "GoTrue"})
    assert "PASS" in run_check(handler)


@pytest.mark.parametrize("status", [401, 403, 500, 302])
def test_bad_statuses_do_not_leak_response(status):
    with pytest.raises(ConnectionCheckError) as caught:
        run_check(lambda request: httpx.Response(status, text="private body"))
    assert "private body" not in str(caught.value)


def test_transport_error_is_sanitized():
    def handler(request):
        raise httpx.ConnectError("private URL", request=request)
    with pytest.raises(ConnectionCheckError, match="Could not reach"):
        run_check(handler)


def test_unexpected_health_response_is_rejected():
    with pytest.raises(ConnectionCheckError, match="unexpected"):
        run_check(lambda request: httpx.Response(200, json={}))


def test_authenticated_read_validates_user_before_table():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer user-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": "user-a"})
        assert request.url.params["limit"] == "1"
        assert "content" not in request.url.params["select"]
        return httpx.Response(200, json=[])
    assert "PASS" in run_check(handler, documents=True)
    assert paths == ["/auth/v1/user", "/rest/v1/documents"]


def test_expired_token_does_not_query_documents():
    def handler(request):
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(401)
    with pytest.raises(ConnectionCheckError, match="invalid or expired"):
        run_check(handler, documents=True)


def test_missing_table_has_actionable_error():
    def handler(request):
        return httpx.Response(200, json={"id": "user-a"}) if request.url.path == "/auth/v1/user" else httpx.Response(404)
    with pytest.raises(ConnectionCheckError, match="apply the migration"):
        run_check(handler, documents=True)


def test_store_factory_uses_configuration_and_closes_client():
    async def run():
        async with open_document_store("user-token", SETTINGS) as store:
            assert store._url == "https://example.supabase.co/rest/v1/documents"
            client = store._client
            assert not client.is_closed
        assert client.is_closed
    asyncio.run(run())
