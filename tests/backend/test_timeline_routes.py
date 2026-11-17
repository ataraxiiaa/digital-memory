"""Timeline build, list and review routes with a mocked Supabase REST API."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_auth_client, get_auth_settings
from backend.config import SupabaseSettings
from backend.main import app
from test_source_passages import report_for

OWNER = "00000000-0000-0000-0000-000000000001"
DOC = "00000000-0000-0000-0000-000000000010"
RUN = "00000000-0000-0000-0000-000000000020"
EXTRACTION = "00000000-0000-0000-0000-000000000030"
HEADERS = {"Authorization": "Bearer user-token"}
TEXT = "Omar's wedding is on 21 December in Murree."


@pytest.fixture
def api():
    source = report_for([TEXT])
    native = source.native_extraction.model_copy(update={"document_id": UUID(DOC)})
    source = source.model_copy(update={"native_extraction": native})
    region = native.pages[0].regions[0]

    def mention_row(label, span, status="accepted", reviewed_label=None):
        start = TEXT.index(span)
        return {"id": str(uuid4()), "run_id": RUN, "label": label, "reviewed_label": reviewed_label, "text": span,
            "review_status": status, "citation": {"document_id": DOC, "extraction_id": str(native.id),
            "page_number": 1, "region_id": str(region.id), "start_char": start, "end_char": start + len(span)}}

    row = {"id": DOC, "title": "Family notes", "content": "", "document_date": "2026-11-18", "collection": "Unfiled",
        "synthetic": True, "created_at": "2026-11-15T00:00:00Z", "status": "ready", "storage_path": None,
        "original_filename": None, "file_size_bytes": None, "page_count": 1, "processing_run_id": EXTRACTION,
        "processing_started_at": None, "processing_error": None}
    state = {"mentions": [mention_row("event", "wedding"), mention_row("date", "21 December"),
        mention_row("location", "Murree"), mention_row("person", "Omar"),
        mention_row("date", "Omar", status="suggested")],
        "items": [], "calls": [], "links": [], "hidden": False}

    def handler(request):
        path, params = request.url.path, request.url.params
        state["calls"].append(path)
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER, "role": "authenticated"})
        if path == "/rest/v1/documents":
            if "id" in params and params["id"].startswith("in."):
                return httpx.Response(200, json=[{"id": DOC, "title": "Family notes"}])
            if state["hidden"]:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[row])
        if path == "/rest/v1/document_extractions":
            return httpx.Response(200, json=[{"report": source.model_dump(mode="json")}])
        if path == "/rest/v1/entity_runs":
            return httpx.Response(200, json=[{"id": RUN}])
        if path == "/rest/v1/entity_mentions":
            rows = [m for m in state["mentions"] if m["review_status"] == "accepted"]
            return httpx.Response(200, json=rows[int(params.get("offset", 0)):][:int(params["limit"])])
        if path == "/rest/v1/entity_links":
            return httpx.Response(200, json=state["links"])
        body = json.loads(request.content) if request.content else None
        if path == "/rest/v1/rpc/replace_timeline_items":
            kept = [i for i in state["items"] if i["review_status"] != "suggested"]
            ids = {i["id"] for i in body["p_items"]}
            state["items"] = kept
            for item in body["p_items"]:
                if item["id"] in {k["id"] for k in kept}:
                    continue
                state["items"].append({**item, "owner_id": OWNER, "review_status": "suggested",
                    "corrected_title": None, "corrected_start": None, "corrected_end": None,
                    "corrected_precision": None, "stale": False, "reviewed_at": None})
            return httpx.Response(200, json={"written": len(ids), "removed": 0, "stale": 0})
        if path == "/rest/v1/timeline_items":
            rows = state["items"]
            if "review_status" in params:
                rows = [i for i in rows if i["review_status"] == params["review_status"].removeprefix("eq.")]
            return httpx.Response(200, json=rows[int(params.get("offset", 0)):][:int(params["limit"])])
        if path == "/rest/v1/rpc/review_timeline_item":
            item = next((i for i in state["items"] if i["id"] == body["p_id"]), None)
            if item is None:
                return httpx.Response(400, json={"code": "P0002"})
            item.update(review_status=body["p_status"], reviewed_at="2026-11-18T00:00:00Z",
                corrected_title=body["p_title"], corrected_start=body["p_start"],
                corrected_end=body["p_end"], corrected_precision=body["p_precision"])
            return httpx.Response(200, json=[item])
        raise AssertionError(path)

    async def client_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_client] = client_dependency
    app.dependency_overrides[get_auth_settings] = lambda: SupabaseSettings("https://example.supabase.co", "sb_publishable_test")
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_build_pairs_only_accepted_mentions_and_stores_a_dated_suggestion(api):
    client, state = api
    response = client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"documents": 1, "items": 1, "undated": 0, "unattached_dates": 0}
    item, = state["items"]
    assert (item["title"], item["start_date"], item["precision"]) == ("wedding", "2026-12-21", "day")
    assert "year_inferred" in item["flags"]
    assert {ref["role"] for ref in item["refs"]} == {"event", "date", "location", "participant"}
    assert all(ref["text"] != "Omar" or ref["role"] == "participant" for ref in item["refs"])


def test_list_is_dated_ordered_and_hides_rejected_by_default(api):
    client, state = api
    client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    page = client.get("/timeline", headers=HEADERS)
    assert page.headers["cache-control"] == "no-store"
    data = page.json()
    assert data["total"] == 1 and data["items"][0]["document_title"] == "Family notes"
    item_id = data["items"][0]["id"]
    client.patch(f"/timeline/{item_id}", json={"status": "rejected"}, headers=HEADERS)
    assert client.get("/timeline", headers=HEADERS).json()["total"] == 0
    assert client.get("/timeline?status=rejected", headers=HEADERS).json()["total"] == 1


def test_review_confirms_with_a_date_correction_and_rebuild_keeps_it(api):
    client, state = api
    client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    item_id = client.get("/timeline", headers=HEADERS).json()["items"][0]["id"]
    reviewed = client.patch(f"/timeline/{item_id}", headers=HEADERS, json={
        "status": "confirmed", "title": "Omar's wedding", "start": "2027-12-21", "end": "2027-12-21", "precision": "day"})
    assert reviewed.status_code == 200 and reviewed.json()["corrected_start"] == "2027-12-21"
    assert reviewed.json()["start_date"] == "2026-12-21"
    client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    kept = client.get("/timeline", headers=HEADERS).json()["items"][0]
    assert kept["review_status"] == "confirmed" and kept["corrected_start"] == "2027-12-21"


def test_date_range_and_dated_filters(api):
    client, _ = api
    client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    assert client.get("/timeline?after=2026-12-01&before=2026-12-31", headers=HEADERS).json()["total"] == 1
    assert client.get("/timeline?after=2027-01-01", headers=HEADERS).json()["total"] == 0
    assert client.get("/timeline?before=2026-12-20", headers=HEADERS).json()["total"] == 0
    assert client.get("/timeline?dated=false", headers=HEADERS).json()["total"] == 0
    assert client.get("/timeline?dated=true", headers=HEADERS).json()["total"] == 1


def test_entity_filter_uses_links_of_the_cited_mentions(api):
    client, state = api
    client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS)
    person = next(m for m in state["mentions"] if m["text"] == "Omar" and m["review_status"] == "accepted")
    entity = str(uuid4())
    state["links"] = [{"run_id": RUN, "mention_id": person["id"]}]
    assert client.get(f"/timeline?entity_id={entity}", headers=HEADERS).json()["total"] == 1
    state["links"] = []
    assert client.get(f"/timeline?entity_id={entity}", headers=HEADERS).json()["total"] == 0


def test_build_all_skips_unfinished_documents_and_reports_totals(api):
    client, state = api
    response = client.post("/timeline/build", headers=HEADERS)
    assert response.status_code == 200 and response.json()["items"] == 1


@pytest.mark.parametrize("body", [
    {"status": "bogus"}, {"status": "rejected", "title": "x"}, {"status": "confirmed", "start": "2026-01-02"},
    {"status": "confirmed", "start": "2026-01-02", "end": "2026-01-01", "precision": "day"},
    {"status": "confirmed", "start": "2026-01-02", "end": "2026-01-02", "precision": "decade"},
    {"status": "confirmed", "extra": 1}])
def test_review_rejects_invalid_corrections(api, body):
    client, _ = api
    assert client.patch(f"/timeline/{uuid4()}", json=body, headers=HEADERS).status_code == 422


def test_unknown_item_is_not_found_and_auth_is_required(api):
    client, state = api
    assert client.patch(f"/timeline/{uuid4()}", json={"status": "confirmed"}, headers=HEADERS).status_code == 404
    for call in (lambda: client.get("/timeline"), lambda: client.post("/timeline/build"),
                 lambda: client.post(f"/documents/{DOC}/timeline/build"),
                 lambda: client.patch(f"/timeline/{uuid4()}", json={"status": "confirmed"})):
        assert call().status_code == 401


def test_hidden_document_cannot_be_built(api):
    client, state = api
    state["hidden"] = True
    assert client.post(f"/documents/{DOC}/timeline/build", headers=HEADERS).status_code == 404
    assert not any("rpc" in call for call in state["calls"])


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "status=maybe", "after=notadate", "entity_id=x"])
def test_list_rejects_invalid_filters(api, query):
    client, _ = api
    assert client.get(f"/timeline?{query}", headers=HEADERS).status_code == 422
