"""Authentication API contract with mocked Supabase user validation."""

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app


USER = {
    "id": "00000000-0000-0000-0000-000000000001",
    "email": "alex@example.test", "role": "authenticated",
    "email_confirmed_at": "2026-11-13T10:00:00Z",
    "user_metadata": {"private": "not part of public response"},
}


@pytest.fixture
def auth_client():
    calls = []
    result = {"status": 200, "body": USER, "error": None}

    def handler(request):
        calls.append(request)
        if result["error"]:
            raise result["error"]
        return httpx.Response(result["status"], json=result["body"])

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    app.dependency_overrides[get_auth_client] = client_dependency
    try:
        with TestClient(app) as client:
            yield client, calls, result
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_valid_token_returns_verified_user_without_private_metadata(auth_client):
    client, calls, _ = auth_client
    response = client.get("/auth/me", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 200
    assert response.json() == {key: USER[key] for key in ("id", "email", "role", "email_confirmed_at")}
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://example.supabase.co/auth/v1/user"
    assert calls[0].headers["authorization"] == "Bearer user-token"
    assert calls[0].headers["apikey"] == "sb_publishable_test"


@pytest.mark.parametrize("header", [None, "", "Basic token", "Bearer", "Bearer a b", "Bearer " + "x" * 16385])
def test_missing_or_malformed_tokens_fail_before_network(auth_client, header):
    client, calls, _ = auth_client
    response = client.get("/auth/me", headers={} if header is None else {"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert calls == []


def test_duplicate_authorization_headers_are_rejected(auth_client):
    client, calls, _ = auth_client
    response = client.get("/auth/me", headers=[("Authorization", "Bearer a"), ("Authorization", "Bearer b")])
    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize("token,status", [("invalid-token", 400), ("expired-token", 401), ("revoked-token", 403)])
def test_rejected_tokens_return_generic_401(auth_client, token, status):
    client, _, result = auth_client
    result.update(status=status, body={"message": "sensitive upstream response"})
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "sensitive" not in response.text
    assert token not in response.text


@pytest.mark.parametrize("status", [429, 500, 503, 302])
def test_provider_failure_is_not_reported_as_bad_credentials(auth_client, status):
    client, _, result = auth_client
    result.update(status=status, body={"message": "private"})
    response = client.get("/auth/me", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 503
    assert "private" not in response.text


def test_timeout_returns_service_unavailable(auth_client):
    client, _, result = auth_client
    result["error"] = httpx.ReadTimeout("private diagnostic")
    response = client.get("/auth/me", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 503
    assert "private" not in response.text


@pytest.mark.parametrize("body", [{}, {**USER, "id": "not-a-uuid"}, {**USER, "role": "service_role"}, []])
def test_malformed_provider_identity_is_not_trusted(auth_client, body):
    client, _, result = auth_client
    result["body"] = body
    response = client.get("/auth/me", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 502


def test_missing_config_returns_503_while_health_stays_public(monkeypatch, tmp_path):
    from backend.auth import dependencies
    from backend.config import load_supabase_settings
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.setattr(dependencies, "load_supabase_settings", lambda: load_supabase_settings(tmp_path / "absent"))
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me", headers={"Authorization": "Bearer token"}).status_code == 503
        assert client.get("/health").status_code == 200


def test_openapi_documents_bearer_auth_only_for_protected_route(auth_client):
    client, _, _ = auth_client
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health"]["get"]
