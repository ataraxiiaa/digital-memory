"""Build, browse, and review timeline items derived from accepted entity mentions."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.timeline import BuildResult, TimelineItem, TimelinePage, TimelineReview
from ..services.timeline import build_timeline_items
from ..storage.documents import StorageUnavailable
from ..storage.extractions import SupabaseExtractionStore
from ..storage.timeline import SupabaseTimelineStore
from .documents import Store
from .processing import get_extraction, get_extraction_store

MAX_DOCUMENTS = 100


async def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["Timeline"], dependencies=[Depends(no_store)])


def get_timeline_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseTimelineStore(client, settings, session.access_token.get_secret_value())


Timeline = Annotated[SupabaseTimelineStore, Depends(get_timeline_store)]
Extractions = Annotated[SupabaseExtractionStore, Depends(get_extraction_store)]


async def build_document(document_id: UUID, store: Store, extractions: Extractions, timeline: SupabaseTimelineStore):
    report = await get_extraction(document_id, store, extractions)
    document = await store.get(document_id)
    mentions = await timeline.accepted_mentions(document_id)
    build = build_timeline_items(report, mentions, document.document_date)
    await timeline.replace(document_id, build.items)
    return build


def summarize(builds) -> BuildResult:
    items = [item for build in builds for item in build.items]
    return BuildResult(documents=len(builds), items=len(items),
        undated=sum(item.date_status != "resolved" for item in items),
        unattached_dates=sum(build.unattached_dates for build in builds))


@router.post("/documents/{document_id}/timeline/build", response_model=BuildResult)
async def build_document_timeline(document_id: UUID, store: Store, extractions: Extractions,
                                  timeline: Timeline) -> BuildResult:
    """Recompute this document's suggestions from its accepted mentions. Reviews are kept."""
    return summarize([await build_document(document_id, store, extractions, timeline)])


@router.post("/timeline/build", response_model=BuildResult)
async def build_all_timelines(store: Store, extractions: Extractions, timeline: Timeline) -> BuildResult:
    """Recompute every finished document. Documents without an extraction are skipped."""
    builds, offset = [], 0
    while offset < MAX_DOCUMENTS:
        page = await store.list(limit=min(50, MAX_DOCUMENTS - offset), offset=offset)
        for summary in page:
            if summary.status not in ("ready", "needs_review"):
                continue
            try:
                builds.append(await build_document(summary.id, store, extractions, timeline))
            except HTTPException as error:
                if error.status_code not in (404, 409):
                    raise
        if len(page) < min(50, MAX_DOCUMENTS - offset):
            break
        offset += len(page)
    return summarize(builds)


def effective_start(item: TimelineItem) -> date | None:
    return item.corrected_start or item.start_date


@router.get("/timeline", response_model=TimelinePage)
async def list_timeline(
    timeline: Timeline,
    document_id: UUID | None = None,
    status: Annotated[Literal["suggested", "confirmed", "rejected"] | None, Query()] = None,
    after: date | None = None,
    before: date | None = None,
    entity_id: UUID | None = None,
    dated: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelinePage:
    """Items in date order; undated items last. Rejected items are hidden unless asked for."""
    items = await timeline.items(document_id=document_id, status=status)
    if status is None:
        items = [item for item in items if item.review_status != "rejected"]
    if entity_id is not None:
        linked = await timeline.linked_mentions(entity_id)
        items = [item for item in items if any((str(ref.run_id), str(ref.mention_id)) in linked for ref in item.refs)]
    undated = sum(effective_start(item) is None for item in items)
    if dated is not None:
        items = [item for item in items if (effective_start(item) is not None) == dated]
    if after or before:
        def end(item):
            return item.corrected_end or item.end_date
        items = [item for item in items if effective_start(item) is not None
            and (before is None or effective_start(item) <= before) and (after is None or end(item) >= after)]
    items.sort(key=lambda item: (effective_start(item) is None, effective_start(item) or date.min,
        item.page_number, item.sentence_start, str(item.id)))
    return TimelinePage(total=len(items), offset=offset, limit=limit, undated=undated,
        items=items[offset:offset + limit])


@router.patch("/timeline/{item_id}", response_model=TimelineItem)
async def review_timeline_item(item_id: UUID, review: TimelineReview, timeline: Timeline) -> TimelineItem:
    item = await timeline.review(item_id, review)
    if item.id != item_id:
        raise StorageUnavailable("Invalid timeline storage response")
    return item
