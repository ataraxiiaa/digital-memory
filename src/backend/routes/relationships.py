"""Build, review, and graph relationships between accepted entity mentions."""

from functools import lru_cache
from importlib.metadata import version
from importlib.util import find_spec
from typing import Annotated, Callable, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.relationships import (
    BuildResult, Graph, RelationBuild, RelationReview, Relationship, RelationshipPage,
)
from ..services.graph import build_graph
from ..services.relations import build_relation_candidates, valid_labels
from ..storage.documents import StorageUnavailable
from ..storage.extractions import SupabaseExtractionStore
from ..storage.relationships import SupabaseRelationshipStore
from .documents import Store
from .processing import get_extraction, get_extraction_store, slots

MAX_DOCUMENTS = 100
RelationKind = Literal["works_at", "located_in", "attended", "part_of", "member_of", "collaborated_with"]


async def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["Relationships"], dependencies=[Depends(no_store)])


@lru_cache(maxsize=1)
def _model():
    from ..services.entity_mentions import load_model
    return load_model()


def get_relation_extractor() -> Callable:
    """The local relation model; shares one cached copy and the single worker slot."""
    if find_spec("gliner2") is None:
        raise HTTPException(503, "Relationship extraction is unavailable. Please try again later.")

    def extract(report, mentions) -> RelationBuild:
        return build_relation_candidates(report, mentions, _model(),
            method=f"gliner2-relations@{version('gliner2')}")
    return extract


def get_relationship_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseRelationshipStore(client, settings, session.access_token.get_secret_value())


Relationships = Annotated[SupabaseRelationshipStore, Depends(get_relationship_store)]
Extractions = Annotated[SupabaseExtractionStore, Depends(get_extraction_store)]
Extractor = Annotated[Callable, Depends(get_relation_extractor)]


async def build_document(document_id: UUID, store: Store, extractions: Extractions,
                         relationships: SupabaseRelationshipStore, extractor: Callable) -> RelationBuild:
    report = await get_extraction(document_id, store, extractions)
    mentions = await relationships.accepted_mentions(document_id)
    build = await run_in_threadpool(extractor, report, mentions) if mentions else RelationBuild(
        candidates=(), counts=build_relation_candidates(report, [], None).counts)
    await relationships.replace(document_id, build.candidates)
    return build


def summarize(builds: list[RelationBuild]) -> BuildResult:
    filtered = sum(b.counts.filtered_negated + b.counts.filtered_hedged + b.counts.filtered_ended + b.counts.filtered_cue
        for b in builds)
    return BuildResult(documents=len(builds), candidates=sum(len(b.candidates) for b in builds), filtered=filtered)


@router.post("/documents/{document_id}/relationships/build", response_model=BuildResult)
async def build_document_relationships(document_id: UUID, store: Store, extractions: Extractions,
                                       relationships: Relationships, extractor: Extractor) -> BuildResult:
    """Recompute this document's suggestions from its accepted mentions. Reviews are kept."""
    if not slots.acquire(blocking=False):
        raise HTTPException(409, "Another job is running. Please try again shortly.")
    try:
        return summarize([await build_document(document_id, store, extractions, relationships, extractor)])
    finally:
        slots.release()


@router.post("/relationships/build", response_model=BuildResult)
async def build_all_relationships(store: Store, extractions: Extractions, relationships: Relationships,
                                  extractor: Extractor) -> BuildResult:
    """Recompute every finished document. Documents without an extraction are skipped."""
    if not slots.acquire(blocking=False):
        raise HTTPException(409, "Another job is running. Please try again shortly.")
    try:
        builds, offset = [], 0
        while offset < MAX_DOCUMENTS:
            size = min(50, MAX_DOCUMENTS - offset)
            page = await store.list(limit=size, offset=offset)
            for summary in page:
                if summary.status not in ("ready", "needs_review"):
                    continue
                try:
                    builds.append(await build_document(summary.id, store, extractions, relationships, extractor))
                except HTTPException as error:
                    if error.status_code not in (404, 409):
                        raise
            if len(page) < size:
                break
            offset += len(page)
        return summarize(builds)
    finally:
        slots.release()


@router.get("/relationships", response_model=RelationshipPage)
async def list_relationships(
    relationships: Relationships,
    document_id: UUID | None = None,
    status: Annotated[Literal["suggested", "confirmed", "rejected"] | None, Query()] = None,
    type: Annotated[RelationKind | None, Query()] = None,
    entity_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RelationshipPage:
    """Relationships in reading order. Rejected ones are hidden unless asked for."""
    items = await relationships.list(document_id=document_id, status=status, type_=type)
    if status is None:
        items = [item for item in items if item.review_status != "rejected"]
    if entity_id is not None:
        linked = await relationships.linked_mentions(entity_id)
        items = [item for item in items if any((str(end.run_id), str(end.mention_id)) in linked
            for end in (item.subject, item.object))]
    return RelationshipPage(total=len(items), offset=offset, limit=limit, relationships=items[offset:offset + limit])


@router.patch("/relationships/{relationship_id}", response_model=Relationship)
async def review_relationship(relationship_id: UUID, review: RelationReview, relationships: Relationships) -> Relationship:
    if review.status == "confirmed" and (review.type is not None or review.swap):
        current = await relationships.get(relationship_id)
        if current is None:
            raise HTTPException(404, "Not found")
        kind = review.type or current.type
        first, second = (current.object, current.subject) if review.swap else (current.subject, current.object)
        if not valid_labels(kind, first.label, second.label):
            raise HTTPException(422, "That type or direction does not fit these two entities.")
    item = await relationships.review(relationship_id, review)
    if item.id != relationship_id:
        raise StorageUnavailable("Invalid relationship storage response")
    return item


@router.get("/graph", response_model=Graph)
async def get_graph(relationships: Relationships, document_id: UUID | None = None) -> Graph:
    """Entity-level graph of confirmed relationships only."""
    confirmed = await relationships.list(document_id=document_id, status="confirmed")
    confirmed = [item for item in confirmed if not item.stale]
    mention_ids = {str(end.mention_id) for item in confirmed for end in (item.subject, item.object)}
    links, entities = await relationships.entities_for_mentions(mention_ids)
    return build_graph(confirmed, links, entities)
