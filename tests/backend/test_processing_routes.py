"""Processing state transitions through authenticated API requests and mocked cloud services."""

from io import BytesIO
import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.routes.processing import get_pdf_processor, get_processing_client_factory
from backend.schemas.layout import LayoutReport
from backend.services.pdf_extraction import extract_pdf_text


OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
RUN = "00000000-0000-0000-0000-000000000020"
HEADERS = {"Authorization": "Bearer user-token"}


def pdf_bytes():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 700 Td (Budget approved.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def api():
    data = pdf_bytes()
    row = {"id": DOC, "title": "Test PDF", "content": "", "document_date": None,
        "collection": "Unfiled", "synthetic": True, "created_at": "2026-11-15T00:00:00Z",
        "status": "uploaded", "storage_path": f"{OWNER}/{DOC}/original.pdf",
        "original_filename": "test.pdf", "file_size_bytes": len(data), "page_count": 1,
        "processing_run_id": None, "processing_started_at": None, "processing_error": None}
    state = {"row": row, "hidden": False, "report": None, "fail": False,
        "review": False, "commit_timeout": False, "file_status": 200, "stale": False}
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer user-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if request.url.path == "/rest/v1/documents":
            return httpx.Response(200, json=[] if state["hidden"] else [row])
        if "/storage/v1/object/authenticated/" in request.url.path:
            return httpx.Response(state["file_status"], content=data)
        if request.url.path == "/rest/v1/rpc/start_document_extraction":
            assert json.loads(request.content) == {"p_document_id": DOC}
            if row["status"] == "processing":
                return httpx.Response(400, json={"code": "55000", "message": "private detail"})
            row.update(status="processing", processing_run_id=RUN, processing_started_at="2026-11-15T00:00:00Z")
            return httpx.Response(200, json=RUN)
        if request.url.path == "/rest/v1/rpc/finish_document_extraction":
            payload = json.loads(request.content)
            assert payload["p_run_id"] == RUN
            if row["status"] != "processing" or state["stale"]:
                return httpx.Response(200, json=False)
            row["status"] = payload["p_status"]
            row["processing_error"] = "Extraction could not finish. Please retry." if row["status"] == "failed" else None
            state["report"] = payload["p_report"]
            if state["commit_timeout"] and payload["p_report"]:
                raise httpx.ReadTimeout("sensitive provider detail")
            return httpx.Response(200, json=True)
        if request.url.path == "/rest/v1/document_extractions":
            assert request.url.params["id"] == f"eq.{RUN}"
            return httpx.Response(200, json=[{"report": state["report"]}])
        raise AssertionError(request.url.path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    def processor(data, *, document_id):
        assert row["status"] == "processing"
        if state["fail"]:
            raise ValueError("sensitive local path and document content")
        native = extract_pdf_text(data, document_id=document_id)
        return LayoutReport(native_extraction=native, detector="test", model_repository="test",
            model_revision="1", tool_versions={"test": "1"}, options={}, pages=[{
                "page_number": 1, "status": "needs_review" if state["review"] else "detected",
                "warnings": ["Review table"] if state["review"] else [],
            }])

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    app.dependency_overrides[get_pdf_processor] = lambda: processor
    app.dependency_overrides[get_processing_client_factory] = lambda: lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with TestClient(app) as client:
            yield client, state, calls
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_process_persists_report_and_ready_status(api):
    client, state, _ = api
    response = client.post(f"/documents/{DOC}/process", headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    assert client.get(f"/documents/{DOC}", headers=HEADERS).json()["status"] == "ready"
    assert response.json()["processing_run_id"] == RUN
    assert state["report"]["native_extraction"]["pages"][0]["regions"][0]["text"] == "Budget approved."
    saved = client.get(f"/documents/{DOC}/extraction", headers=HEADERS)
    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json() == state["report"]


def test_uncertain_content_finishes_needs_review(api):
    client, state, _ = api
    state["review"] = True
    response = client.post(f"/documents/{DOC}/process", headers=HEADERS)
    assert response.status_code == 202
    assert client.get(f"/documents/{DOC}", headers=HEADERS).json()["status"] == "needs_review"


def test_processing_failure_is_persisted_and_sanitized(api):
    client, state, _ = api
    state["fail"] = True
    response = client.post(f"/documents/{DOC}/process", headers=HEADERS)
    assert response.status_code == 202
    assert "sensitive" not in response.text
    assert state["row"]["status"] == "failed"
    assert state["report"] is None


def test_timeout_after_commit_does_not_overwrite_saved_result(api):
    client, state, _ = api
    state["commit_timeout"] = True
    assert client.post(f"/documents/{DOC}/process", headers=HEADERS).status_code == 202
    assert state["row"]["status"] == "ready"
    assert state["report"] is not None
    assert client.get(f"/documents/{DOC}", headers=HEADERS).json()["status"] == "ready"


def test_hidden_document_does_not_start_processing_or_read_results(api):
    client, state, calls = api
    state["hidden"] = True
    assert client.post(f"/documents/{DOC}/process", headers=HEADERS).status_code == 404
    assert client.get(f"/documents/{DOC}/extraction", headers=HEADERS).status_code == 404
    assert not any("/rpc/" in request.url.path for request in calls)


def test_duplicate_start_and_stale_finish_are_conflicts(api):
    client, state, _ = api
    state["row"]["status"] = "processing"
    assert client.post(f"/documents/{DOC}/process", headers=HEADERS).status_code == 409
    state["row"]["status"] = "uploaded"
    state["stale"] = True
    assert client.post(f"/documents/{DOC}/process", headers=HEADERS).status_code == 202


def test_failed_original_download_records_failure(api):
    client, state, _ = api
    state["file_status"] = 404
    assert client.post(f"/documents/{DOC}/process", headers=HEADERS).status_code == 202
    assert state["row"]["status"] == "failed"


def test_processing_requires_authentication(api):
    client, state, _ = api
    assert client.post(f"/documents/{DOC}/process").status_code == 401
    assert state["row"]["status"] == "uploaded"
