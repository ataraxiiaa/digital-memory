"""Note preparation route against a mocked Supabase REST API, then the existing entity routes."""

import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.routes.entities import get_entity_extractor
from backend.routes.processing import get_processing_client_factory
from backend.services.entity_mentions import LABEL_SCHEMAS, extract_entity_mentions
from backend.services.text_notes import note_sha256
from test_entity_mentions import FakeModel, candidate

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
HEADERS = {"Authorization": "Bearer user-token"}
TEXT = "Sara approved Project Atlas on 3 March 2024.\r\n\r\nCafé meeting in Lahore."


@pytest.fixture
def api():
    row = {"id": DOC, "title": "Meeting note", "content": TEXT, "document_date": None,
        "collection": "Notes", "synthetic": True, "created_at": "2026-11-18T00:00:00Z",
        "status": "ready", "storage_path": None, "original_filename": None,
        "file_size_bytes": None, "page_count": None, "processing_run_id": None,
        "processing_started_at": None, "processing_error": None}
    state = {"row": row, "hidden": False, "extraction": None, "runs": [], "mentions": [],
        "rpc_calls": [], "reject": None}
    model = FakeModel({"entities": {}})

    def handler(request):
        path = request.url.path
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if path == "/rest/v1/documents":
            return httpx.Response(200, json=[] if state["hidden"] else [state["row"]])
        body = json.loads(request.content) if request.content else None
        if path == "/rest/v1/rpc/create_text_extraction":
            state["rpc_calls"].append(body)
            if state["reject"]:
                return httpx.Response(400, json={"code": state["reject"]})
            report = body["p_report"]
            # Mirror the database: the report must match the stored note exactly.
            native = report["native_extraction"]
            assert native["document_id"] == DOC
            assert native["source_sha256"] == note_sha256(state["row"]["content"])
            assert native["pages"][0]["regions"][0]["text"] == state["row"]["content"]
            existing = state["extraction"]
            if existing and existing["report"]["native_extraction"]["source_sha256"] == native["source_sha256"]:
                return httpx.Response(200, json=existing["id"])
            state["extraction"] = {"id": body["p_run_id"], "report": report}
            state["row"]["processing_run_id"] = body["p_run_id"]
            return httpx.Response(200, json=body["p_run_id"])
        if path == "/rest/v1/document_extractions":
            return httpx.Response(200, json=[{"report": state["extraction"]["report"]}])
        if path == "/rest/v1/rpc/start_entity_run":
            state["runs"].append({"id": body["p_run_id"], "document_id": DOC,
                "extraction_id": state["row"]["processing_run_id"], "status": "processing",
                "started_at": "2026-11-18T00:00:00Z", "finished_at": None})
            return httpx.Response(200, json=body["p_run_id"])
        if path == "/rest/v1/rpc/finish_entity_run":
            run = state["runs"][-1]
            run["status"] = body["p_status"]
            run["finished_at"] = "2026-11-18T00:01:00Z"
            run["label_schema_version"] = body["p_meta"]["label_schema_version"]
            for m in body["p_mentions"]:
                state["mentions"].append({**m, "run_id": run["id"], "document_id": DOC,
                    "page_number": 1, "start_char": m["citation"]["start_char"],
                    "end_char": m["citation"]["end_char"], "review_status": "suggested",
                    "reviewed_label": None, "reviewed_at": None})
            return httpx.Response(200, json=True)
        if path == "/rest/v1/entity_runs":
            return httpx.Response(200, json=state["runs"][-1:])
        if path == "/rest/v1/entity_mentions":
            if request.url.params["select"] == "*":
                rows = state["mentions"]
                return httpx.Response(200, json=rows, headers={"content-range": f"0-0/{len(rows)}"})
            return httpx.Response(200, json=state["mentions"])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    def extractor(report, schema="full-v1"):
        return extract_entity_mentions(report, model, labels=LABEL_SCHEMAS[schema])

    state["model"] = model
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


def test_prepare_attaches_an_exact_text_extraction(api):
    client, state = api
    response = client.post(f"/documents/{DOC}/process-text", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["processing_run_id"] == state["extraction"]["id"]
    saved = client.get(f"/documents/{DOC}/extraction", headers=HEADERS).json()
    assert saved["native_extraction"]["pages"][0]["regions"][0]["text"] == TEXT
    assert saved["native_extraction"]["source_sha256"] == note_sha256(TEXT)


def test_repeating_preparation_keeps_the_same_extraction(api):
    client, state = api
    first = client.post(f"/documents/{DOC}/process-text", headers=HEADERS).json()["processing_run_id"]
    second = client.post(f"/documents/{DOC}/process-text", headers=HEADERS).json()["processing_run_id"]
    assert first == second and len(state["rpc_calls"]) == 2


def test_changed_text_gets_a_new_extraction_with_new_region_ids(api):
    client, state = api
    first = client.post(f"/documents/{DOC}/process-text", headers=HEADERS).json()["processing_run_id"]
    old_region = state["extraction"]["report"]["native_extraction"]["pages"][0]["regions"][0]["id"]
    state["row"]["content"] = TEXT + " Edited."
    second = client.post(f"/documents/{DOC}/process-text", headers=HEADERS).json()["processing_run_id"]
    new_region = state["extraction"]["report"]["native_extraction"]["pages"][0]["regions"][0]["id"]
    assert first != second and old_region != new_region


def test_entities_run_on_a_note_with_citations_into_the_note_text(api):
    client, state = api
    client.post(f"/documents/{DOC}/process-text", headers=HEADERS)
    start = TEXT.index("Café")
    state["model"].result = {"entities": {"person": [candidate("Sara", 0, 4, .95)],
        "location": [candidate("Lahore", TEXT.index("Lahore"), TEXT.index("Lahore") + 6, .9)],
        "event": [candidate("Café meeting", start, start + 12, .8)]}}
    assert client.post(f"/documents/{DOC}/entities", headers=HEADERS).status_code == 202
    page = client.get(f"/documents/{DOC}/entities", headers=HEADERS).json()
    assert page["run"]["status"] == "completed"
    assert {m["label"] for m in page["mentions"]} == {"person", "location", "event"}
    for mention in page["mentions"]:
        c = mention["citation"]
        assert TEXT[c["start_char"]:c["end_char"]] == mention["text"]
        assert c["document_id"] == DOC


def test_entities_require_preparation_first(api):
    client, _ = api
    state_row_response = client.post(f"/documents/{DOC}/entities", headers=HEADERS)
    assert state_row_response.status_code == 404


def test_pdf_documents_are_refused(api):
    client, state = api
    state["row"]["storage_path"] = f"{OWNER}/{DOC}/original.pdf"
    assert client.post(f"/documents/{DOC}/process-text", headers=HEADERS).status_code == 422
    assert state["rpc_calls"] == []


def test_hidden_document_missing_auth_and_database_refusal(api):
    client, state = api
    assert client.post(f"/documents/{DOC}/process-text").status_code == 401
    state["reject"] = "P0002"
    assert client.post(f"/documents/{DOC}/process-text", headers=HEADERS).status_code == 404
    state["reject"] = "22023"
    failed = client.post(f"/documents/{DOC}/process-text", headers=HEADERS)
    assert failed.status_code == 503 and "22023" not in failed.text
    state["hidden"] = True
    state["rpc_calls"].clear()
    assert client.post(f"/documents/{DOC}/process-text", headers=HEADERS).status_code == 404
    assert state["rpc_calls"] == []


def test_unusable_stored_text_is_a_validation_error(api):
    client, state = api
    state["row"]["content"] = "   "
    assert client.post(f"/documents/{DOC}/process-text", headers=HEADERS).status_code == 422
    assert state["rpc_calls"] == []


def test_migration_is_wrapped_private_and_verifies_the_report_against_the_note():
    from backend.supabase.migrate import MIGRATIONS, transaction_body
    body = transaction_body((MIGRATIONS / "202611180001_text_notes.sql").read_text(encoding="utf-8"))
    assert "security definer set search_path = ''" in body
    assert "revoke all on function public.create_text_extraction(uuid, uuid, jsonb) from public, anon" in body
    assert "grant execute on function public.create_text_extraction(uuid, uuid, jsonb) to authenticated" in body
    assert "owner_id = auth.uid()" in body and "storage_path is not null" in body
    assert "sha256(convert_to(d.content" in body and "{native_extraction,pages,0,regions,0,text}" in body
    assert UUID(DOC)
