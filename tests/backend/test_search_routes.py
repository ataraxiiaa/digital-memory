"""Search routes with a mocked Supabase REST API."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from backend.services.search import START, STOP
from test_source_passages import report_for

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
UPLOADED = "00000000-0000-0000-0000-000000000011"
RUN = "00000000-0000-0000-0000-000000000020"
HEADERS = {"Authorization": "Bearer user-token"}


def document(identifier, title, status, run):
    return {"id": identifier, "title": title, "content": "", "document_date": None, "collection": "Unfiled",
        "synthetic": True, "created_at": "2026-11-15T00:00:00Z", "status": status,
        "storage_path": f"{OWNER}/{identifier}/original.pdf", "original_filename": "n.pdf",
        "file_size_bytes": 10, "page_count": 1, "processing_run_id": run,
        "processing_started_at": None, "processing_error": None}


@pytest.fixture
def api():
    source = report_for(["Budget approved. Travel is covered by the team.", "Second page about sardines."])
    source = source.model_copy(update={"native_extraction": source.native_extraction.model_copy(
        update={"document_id": UUID(DOC)})})
    state = {"rows": [document(DOC, "Q3 notes", "ready", RUN), document(UPLOADED, "Raw upload", "uploaded", None)],
        "hidden": False, "index": {}, "replaced": [], "rpc": [], "fail": None, "stale": False,
        "passages": [], "entities": [], "timeline": []}
    citation = {"document_id": DOC, "extraction_id": str(source.native_extraction.id), "page_number": 1,
        "region_id": str(source.native_extraction.pages[0].regions[0].id), "start_char": 0, "end_char": 15}

    def handler(request):
        path = request.url.path
        params = request.url.params
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if path == "/rest/v1/documents":
            rows = [] if state["hidden"] else state["rows"]
            if "id" in params and params["id"].startswith("eq."):
                rows = [row for row in rows if row["id"] == params["id"][3:]]
            elif "id" in params and params["id"].startswith("in."):
                wanted = params["id"][4:-1].split(",")
                rows = [row for row in rows if row["id"] in wanted]
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/document_extractions":
            return httpx.Response(200, json=[{"report": source.model_dump(mode="json")}])
        if path == "/rest/v1/search_index_state":
            return httpx.Response(200, json=list(state["index"].values()))
        if path.startswith("/rest/v1/rpc/"):
            name = path.rsplit("/", 1)[1]
            body = json.loads(request.content)
            state["rpc"].append((name, body))
            if state["fail"] == name:
                return httpx.Response(500, json={"message": "sensitive upstream detail"})
            if name == "replace_search_passages":
                state["replaced"].append(body)
                run = body["p_extraction_id"]
                state["index"][body["p_document_id"]] = {"document_id": body["p_document_id"],
                    "extraction_id": RUN if not state["stale"] else str(uuid4()),
                    "passage_count": len(body["p_passages"]), "indexed_at": "2026-11-17T10:00:00Z"}
                return httpx.Response(200, json={"passages": len(body["p_passages"])})
            if name == "search_passages_query":
                return httpx.Response(200, json=state["passages"])
            if name == "search_entities_query":
                return httpx.Response(200, json=state["entities"])
            if name == "search_timeline_query":
                return httpx.Response(200, json=state["timeline"])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    try:
        with TestClient(app) as client:
            yield client, state, citation
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def passage_row(citation, snippet, rank=0.4, total=1):
    return {"id": str(uuid4()), "document_id": DOC, "page_number": 1, "citation": citation,
        "review_required": False, "rank": rank, "snippet": snippet, "total": total}


def test_indexing_sends_exact_cited_passages_for_the_current_extraction(api):
    client, state, _ = api
    response = client.post(f"/documents/{DOC}/search-index", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"documents": 1, "passages": 2, "skipped": 0}
    body = state["replaced"][0]
    assert body["p_document_id"] == DOC and body["p_extraction_id"] == RUN
    assert [row["text"] for row in body["p_passages"]] == ["Budget approved. Travel is covered by the team.", "Second page about sardines."]
    assert all(row["citation"]["document_id"] == DOC for row in body["p_passages"])


def test_indexing_needs_an_extracted_visible_document(api):
    client, state, _ = api
    assert client.post(f"/documents/{UPLOADED}/search-index", headers=HEADERS).status_code == 409
    assert client.post(f"/documents/{uuid4()}/search-index", headers=HEADERS).status_code == 404
    state["hidden"] = True
    assert client.post(f"/documents/{DOC}/search-index", headers=HEADERS).status_code == 404
    assert not state["replaced"]


def test_reindexing_everything_skips_unextracted_documents(api):
    client, state, _ = api
    response = client.post("/search/index", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"documents": 1, "passages": 2, "skipped": 1}


def test_status_reports_missing_current_stale_and_not_ready(api):
    client, state, _ = api
    first = client.get("/search/status", headers=HEADERS).json()
    assert {item["title"]: item["status"] for item in first["documents"]} == {"Q3 notes": "missing", "Raw upload": "not_ready"}
    assert (first["current"], first["stale"], first["missing"], first["not_ready"]) == (0, 0, 1, 1)
    client.post(f"/documents/{DOC}/search-index", headers=HEADERS)
    current = client.get("/search/status", headers=HEADERS).json()
    assert current["current"] == 1 and current["documents"][0]["passage_count"] == 2
    state["rows"][0]["processing_run_id"] = str(uuid4())  # the document was re-extracted
    assert client.get("/search/status", headers=HEADERS).json()["stale"] == 1


def test_search_returns_safe_highlight_segments_with_exact_citations(api):
    client, state, citation = api
    state["passages"] = [passage_row(citation, f"<b>{START}Budget{STOP}</b> approved for {START}Marigold{STOP}", total=7)]
    response = client.get("/search", params={"q": "budget marigold", "type": "passages"}, headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["totals"] == {"passages": 7} and data["query"] == "budget marigold"
    hit = data["results"][0]
    assert hit["kind"] == "passage" and hit["document_title"] == "Q3 notes" and hit["page_number"] == 1
    assert hit["citation"]["document_id"] == DOC and hit["citation"]["end_char"] == 15
    assert [(s["text"], s["match"]) for s in hit["snippet"]] == [
        ("<b>", False), ("Budget", True), ("</b> approved for ", False), ("Marigold", True)]
    assert ("search_passages_query", {"p_query": "budget marigold", "p_document_id": None, "p_limit": 20, "p_offset": 0}) in state["rpc"]


def test_all_search_combines_kinds_and_highlights_entity_names(api):
    client, state, citation = api
    state["passages"] = [passage_row(citation, f"{START}Larry{STOP} spoke")]
    state["entities"] = [
        {"kind": "entity", "entity_id": str(uuid4()), "mention_id": None, "run_id": None, "document_id": None,
         "entity_type": "person", "name": "Larry Page", "matched": "L. Page", "status": "confirmed",
         "mention_count": 3, "page_number": None, "citation": None, "total": 2},
        {"kind": "mention", "entity_id": None, "mention_id": str(uuid4()), "run_id": str(uuid4()), "document_id": DOC,
         "entity_type": "person", "name": "Larry", "matched": "Larry", "status": "accepted",
         "mention_count": None, "page_number": 1, "citation": citation, "total": 2}]
    state["timeline"] = [{"id": str(uuid4()), "document_id": DOC, "title": "Larry's wedding", "date_text": "21 December",
        "start_date": "2026-12-21", "page_number": 1, "review_status": "suggested", "sentence_text": "x", "total": 1}]
    data = client.get("/search", params={"q": "larry"}, headers=HEADERS).json()
    assert data["totals"] == {"passages": 1, "entities": 2, "timeline": 1}
    kinds = [hit["kind"] for hit in data["results"]]
    assert kinds == ["entity", "mention", "timeline", "passage"]
    entity = data["results"][0]
    assert entity["entity_type"] == "person" and entity["mention_count"] == 3 and entity["citation"] is None
    assert [(s["text"], s["match"]) for s in entity["snippet"]] == [("Larry", True), (" Page", False)]
    assert data["results"][1]["citation"]["document_id"] == DOC and data["results"][1]["document_title"] == "Q3 notes"
    assert data["results"][2]["date_text"] == "21 December" and data["results"][2]["start_date"] == "2026-12-21"


def test_paging_and_filters_reach_the_database(api):
    client, state, _ = api
    client.get("/search", params={"q": "budget", "type": "passages", "document_id": DOC, "limit": 5, "offset": 10}, headers=HEADERS)
    assert ("search_passages_query", {"p_query": "budget", "p_document_id": DOC, "p_limit": 5, "p_offset": 10}) in state["rpc"]


@pytest.mark.parametrize("params,status", [
    ({"q": ""}, 422), ({"q": "   "}, 422), ({"q": "x" * 201}, 422), ({}, 422),
    ({"q": "a", "limit": 0}, 422), ({"q": "a", "limit": 51}, 422), ({"q": "a", "offset": -1}, 422),
    ({"q": "a", "type": "everything"}, 422), ({"q": "a", "type": "entities", "document_id": DOC}, 422),
    ({"q": "a", "document_id": "not-a-uuid"}, 422)])
def test_invalid_searches_are_rejected_before_the_database(api, params, status):
    client, state, _ = api
    assert client.get("/search", params=params, headers=HEADERS).status_code == status
    assert not any(name.startswith("search_") for name, _ in state["rpc"])


def test_query_text_is_only_ever_sent_as_a_parameter(api):
    client, state, _ = api
    hostile = "'; drop table documents; -- \" OR 1=1"
    assert client.get("/search", params={"q": hostile, "type": "passages"}, headers=HEADERS).status_code == 200
    assert state["rpc"][-1][1]["p_query"] == hostile


def test_authentication_is_required_everywhere(api):
    client, state, _ = api
    for method, path in (("get", "/search?q=a"), ("get", "/search/status"), ("post", "/search/index"),
                         ("post", f"/documents/{DOC}/search-index")):
        assert getattr(client, method)(path).status_code == 401
    assert not state["rpc"]


def test_storage_failures_are_sanitized(api):
    client, state, _ = api
    state["fail"] = "search_passages_query"
    response = client.get("/search", params={"q": "budget", "type": "passages"}, headers=HEADERS)
    assert response.status_code == 503 and "sensitive" not in response.text
    state["fail"] = "replace_search_passages"
    response = client.post(f"/documents/{DOC}/search-index", headers=HEADERS)
    assert response.status_code == 503 and "sensitive" not in response.text


def test_malformed_storage_rows_are_a_service_error_not_a_crash(api):
    client, state, _ = api
    state["passages"] = [{"id": "x", "document_id": DOC}]
    assert client.get("/search", params={"q": "a", "type": "passages"}, headers=HEADERS).status_code == 503
