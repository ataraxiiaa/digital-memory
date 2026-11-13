"""In-memory parser fixtures and mocked upload transactions; no real files sent."""

from io import BytesIO
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.services import pdf_uploads


USER_ID = "00000000-0000-0000-0000-000000000001"
HEADERS = {"Authorization": "Bearer user-token"}


def pdf_bytes(*, encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("test-only")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def api():
    calls = []
    state = {"storage_status": 200, "database_status": 201, "database_timeout": False}

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer user-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": USER_ID, "role": "authenticated"})
        if request.method == "DELETE":
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/storage/v1/object/documents/"):
            assert request.content.startswith(b"%PDF-")
            assert request.headers["x-upsert"] == "false"
            assert request.headers["content-type"] == "application/pdf"
            assert request.url.path.split("/")[5] == USER_ID
            return httpx.Response(state["storage_status"], json={"Key": "stored"})
        assert request.url.path == "/rest/v1/documents"
        if state["database_timeout"]:
            raise httpx.ReadTimeout("private information")
        payload = json.loads(request.content)
        assert "owner_id" not in payload
        assert payload["storage_path"] == f"{USER_ID}/{payload['id']}/original.pdf"
        return httpx.Response(state["database_status"], json=[{**payload, "document_date": None, "created_at": "2026-11-13T12:00:00Z"}])

    async def get_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = get_client
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    try:
        with TestClient(app) as client:
            yield client, calls, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_upload_saves_private_original_and_linked_metadata(api):
    client, calls, _ = api
    data = pdf_bytes()
    response = client.post("/documents/upload", headers=HEADERS, files={"file": ("report.pdf", data, "application/pdf")}, data={"title": "My report", "collection": "Work"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "My report"
    assert body["status"] == "uploaded"
    assert body["content"] == ""
    assert body["file_size_bytes"] == len(data)
    assert body["page_count"] == 1
    assert body["original_filename"] == "report.pdf"
    assert response.headers["location"] == f"/documents/{body['id']}"
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 3


@pytest.mark.parametrize("filename,data,mime,status", [
    ("a.txt", b"hello", "text/plain", 415),
    ("a.pdf", b"hello", "application/pdf", 422),
    ("a.pdf", b"", "application/pdf", 422),
    ("a.pdf", b"%PDF-broken", "application/pdf", 422),
    ("a.pdf", pdf_bytes(encrypted=True), "application/pdf", 422),
])
def test_bad_files_never_reach_storage(api, filename, data, mime, status):
    client, calls, _ = api
    response = client.post("/documents/upload", headers=HEADERS, files={"file": (filename, data, mime)})
    assert response.status_code == status
    assert all(request.url.path == "/auth/v1/user" for request in calls)


def test_oversized_file_is_rejected_during_read(api, monkeypatch):
    client, calls, _ = api
    monkeypatch.setattr(pdf_uploads, "MAX_PDF_BYTES", 10)
    response = client.post("/documents/upload", headers=HEADERS, files={"file": ("a.pdf", pdf_bytes(), "application/pdf")})
    assert response.status_code == 413
    assert len(calls) == 1


def test_unauthed_upload_does_not_reach_supabase(api):
    client, calls, _ = api
    assert client.post("/documents/upload", files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).status_code == 401
    assert calls == []


def test_storage_failure_never_creates_metadata(api):
    client, calls, state = api
    state["storage_status"] = 403
    assert client.post("/documents/upload", headers=HEADERS, files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).status_code == 503
    assert len(calls) == 2


@pytest.mark.parametrize("timeout", [False, True])
def test_metadata_failure_attempts_cleanup(api, timeout):
    client, calls, state = api
    state["database_status"] = 500
    state["database_timeout"] = timeout
    response = client.post("/documents/upload", headers=HEADERS, files={"file": ("a.pdf", pdf_bytes(), "application/pdf")})
    assert response.status_code == 503
    assert "private information" not in response.text
    assert calls[-1].method == "DELETE"
    assert json.loads(calls[-1].content)["prefixes"][0].startswith(USER_ID + "/")


def test_pdf_page_limit(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(pdf_uploads, "MAX_PDF_PAGES", 0)
    assert client.post("/documents/upload", headers=HEADERS, files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).status_code == 422


def test_filename_is_not_used_as_storage_path(api):
    client, _, _ = api
    response = client.post("/documents/upload", headers=HEADERS, files={"file": ("../report.pdf", pdf_bytes(), "application/octet-stream")})
    assert response.status_code == 201
    assert response.json()["original_filename"] == "report.pdf"
    assert ".." not in response.json()["storage_path"]
