"""Keyword search with exact citations, plus explicit index freshness."""

import asyncio
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.extraction import CitationReference
from ..schemas.search import (
    DocumentIndexStatus, IndexResult, IndexStatus, SearchHit, SearchResponse, SearchType,
)
from ..services.search import MAX_QUERY_CHARS, highlight_words, passage_rows, query_words, snippet_segments
from ..storage.documents import StorageUnavailable
from ..storage.extractions import SupabaseExtractionStore
from ..storage.search import SupabaseSearchStore
from .documents import Store
from .processing import get_extraction, get_extraction_store

MAX_DOCUMENTS = 100


async def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["Search"], dependencies=[Depends(no_store)])


def get_search_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseSearchStore(client, settings, session.access_token.get_secret_value())


Search = Annotated[SupabaseSearchStore, Depends(get_search_store)]
Extractions = Annotated[SupabaseExtractionStore, Depends(get_extraction_store)]


async def index_document(document_id: UUID, store: Store, extractions: Extractions, search: SupabaseSearchStore) -> int:
    document = await store.get(document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.processing_run_id is None or document.status not in ("ready", "needs_review"):
        raise HTTPException(409, "Extract this document before indexing it.")
    report = await get_extraction(document_id, store, extractions)
    try:
        rows, _ = passage_rows(report)
    except ValueError:
        raise HTTPException(409, "The saved extraction is not ready for search.") from None
    return await search.replace(document_id, document.processing_run_id, rows)


@router.post("/documents/{document_id}/search-index", response_model=IndexResult)
async def index_one(document_id: UUID, store: Store, extractions: Extractions, search: Search) -> IndexResult:
    """Rebuild this document's search passages from its current saved extraction."""
    return IndexResult(documents=1, passages=await index_document(document_id, store, extractions, search))


@router.post("/search/index", response_model=IndexResult)
async def index_all(store: Store, extractions: Extractions, search: Search) -> IndexResult:
    """Rebuild every finished document (at most 100). Unextracted documents are skipped."""
    documents = passages = skipped = 0
    offset = 0
    while offset < MAX_DOCUMENTS:
        size = min(50, MAX_DOCUMENTS - offset)
        page = await store.list(limit=size, offset=offset)
        for summary in page:
            if summary.status not in ("ready", "needs_review") or summary.processing_run_id is None:
                skipped += 1
                continue
            try:
                passages += await index_document(summary.id, store, extractions, search)
                documents += 1
            except HTTPException as error:
                if error.status_code not in (404, 409):
                    raise
                skipped += 1
        if len(page) < size:
            break
        offset += len(page)
    return IndexResult(documents=documents, passages=passages, skipped=skipped)


@router.get("/search/status", response_model=IndexStatus)
async def index_status(store: Store, search: Search) -> IndexStatus:
    """Which documents are searchable, and which index is behind its extraction."""
    summaries, offset = [], 0
    while offset < MAX_DOCUMENTS:
        size = min(50, MAX_DOCUMENTS - offset)
        page = await store.list(limit=size, offset=offset)
        summaries.extend(page)
        if len(page) < size:
            break
        offset += len(page)
    states = await search.states()
    documents = []
    for summary in summaries:
        state = states.get(str(summary.id))
        if summary.status not in ("ready", "needs_review") or summary.processing_run_id is None:
            status = "not_ready"
        elif state is None:
            status = "missing"
        else:
            status = "current" if state["extraction_id"] == str(summary.processing_run_id) else "stale"
        documents.append(DocumentIndexStatus(document_id=summary.id, title=summary.title, status=status,
            passage_count=state["passage_count"] if state else 0, indexed_at=state["indexed_at"] if state else None))
    count = lambda name: sum(item.status == name for item in documents)  # noqa: E731
    return IndexStatus(documents=documents, current=count("current"), stale=count("stale"),
        missing=count("missing"), not_ready=count("not_ready"))


def citation(value) -> CitationReference | None:
    try:
        return CitationReference.model_validate(value) if value else None
    except ValueError:
        raise StorageUnavailable("Invalid search storage response") from None


@router.get("/search", response_model=SearchResponse)
async def search_all(
    search: Search,
    q: Annotated[str, Query(max_length=MAX_QUERY_CHARS)],
    type: SearchType = "all",
    document_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> SearchResponse:
    """Ranked matches with exact citations. Quoted phrases and -word exclusion are supported for passages."""
    query = q.strip()
    if not query:
        raise HTTPException(422, "Enter something to search for.")
    want = {"passages", "entities", "timeline"} if type == "all" else {type}
    # Entity and timeline matching is document-agnostic; a document filter narrows passages only.
    if document_id is not None and type in ("entities", "timeline"):
        raise HTTPException(422, "A document filter applies to passage search only.")
    jobs = {}
    if "passages" in want:
        jobs["passages"] = search.passages(query, document_id, limit, offset)
    if "entities" in want and document_id is None:
        jobs["entities"] = search.entities(query, limit, offset)
    if "timeline" in want and document_id is None:
        jobs["timeline"] = search.timeline(query, limit, offset)
    rows = dict(zip(jobs, await asyncio.gather(*jobs.values())))
    titles = await search.titles({str(row["document_id"]) for group in rows.values() for row in group if row.get("document_id")})
    words = query_words(query)
    results: list[SearchHit] = []
    totals = {name: 0 for name in want}
    try:
        for row in rows.get("entities", []):
            totals["entities"] = int(row["total"])
            mention = row["kind"] == "mention"
            results.append(SearchHit(kind="mention" if mention else "entity", snippet=highlight_words(row["name"], words),
                entity_id=row["entity_id"], mention_id=row["mention_id"], run_id=row["run_id"],
                document_id=row["document_id"], document_title=titles.get(str(row["document_id"])),
                page_number=row["page_number"], citation=citation(row["citation"]),
                entity_type=row["entity_type"], entity_status=row["status"], mention_count=row["mention_count"]))
        for row in rows.get("timeline", []):
            totals["timeline"] = int(row["total"])
            results.append(SearchHit(kind="timeline", snippet=highlight_words(row["title"], words),
                document_id=row["document_id"], document_title=titles.get(str(row["document_id"])),
                page_number=row["page_number"], timeline_item_id=row["id"], date_text=row["date_text"],
                start_date=row["start_date"], review_status=row["review_status"]))
        for row in rows.get("passages", []):
            totals["passages"] = int(row["total"])
            results.append(SearchHit(kind="passage", score=float(row["rank"]), snippet=snippet_segments(row["snippet"]),
                document_id=row["document_id"], document_title=titles.get(str(row["document_id"])),
                page_number=row["page_number"], citation=citation(row["citation"]),
                review_required=row["review_required"]))
    except (KeyError, TypeError, ValueError):
        raise StorageUnavailable("Invalid search storage response") from None
    return SearchResponse(query=query, type=type, totals=totals, offset=offset, limit=limit, results=results)
