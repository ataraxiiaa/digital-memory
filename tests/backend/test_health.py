"""Request-level checks for the API foundation."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_successful_json(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok", "service": "digital-memory-api"}


def test_unknown_endpoint_returns_not_found(client):
    assert client.get("/documents").status_code == 404


def test_health_rejects_unsupported_method(client):
    assert client.post("/health", json={}).status_code == 405


def test_api_schema_exposes_health_contract(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/health" in schema["paths"]
    assert schema["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/HealthResponse")


def test_interactive_docs_are_available(client):
    response = client.get("/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
