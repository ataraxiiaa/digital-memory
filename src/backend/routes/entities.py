"""Run local entity extraction on a saved extraction and review its suggestions."""

from functools import lru_cache
from importlib.util import find_spec
import logging
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.entities import EntityReport
from ..schemas.entity_review import EntityPage, EntityRun, ReviewRequest, StoredMention
from ..storage.documents import StorageUnavailable
from ..storage.entities import SupabaseEntityStore
from ..storage.extractions import SupabaseExtractionStore
from .documents import Store
from .processing import get_extraction, get_extraction_store, get_processing_client_factory, slots


router = APIRouter(prefix="/documents", tags=["Entities"])
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _model():
    from ..services.entity_mentions import load_model
    return load_model()


def get_entity_extractor() -> Callable:
    if find_spec("gliner2") is None:
        raise HTTPException(503, "Entity extraction is unavailable. Please try again later.")

    def extract(report, schema: str = "full-v1") -> EntityReport:
        from importlib.metadata import version
        from ..services.entity_mentions import LABEL_SCHEMAS, extract_entity_mentions
        return extract_entity_mentions(report, _model(), library_version=version("gliner2"),
            labels=LABEL_SCHEMAS[schema])
    return extract


def get_entity_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseEntityStore(client, settings, session.access_token.get_secret_value())


Entities = Annotated[SupabaseEntityStore, Depends(get_entity_store)]


def entity_status(report: EntityReport) -> str:
    return "needs_review" if any(p.status != "completed" for p in report.passages) else "completed"


async def run_entity_attempt(document_id, run_id, report, schema, session, settings, extractor, client_factory):
    try:
        async with client_factory() as client:
            entities = SupabaseEntityStore(client, settings, session.access_token.get_secret_value())
            try:
                result = await run_in_threadpool(extractor, report, schema)
                if result.document_id != document_id:
                    raise ValueError("Entity source mismatch")
                if result.passages and all(p.status == "failed" for p in result.passages):
                    raise ValueError("No passage completed")
                await entities.finish(run_id, entity_status(result), result)
            except Exception:
                try:
                    await entities.finish(run_id, "failed")
                except (StorageUnavailable, HTTPException):
                    logger.warning("Entity outcome could not be persisted; retry after lease expiry")
    except Exception:
        logger.warning("Entity worker could not initialize; retry after lease expiry")
    finally:
        slots.release()


@router.post("/{document_id}/entities", response_model=EntityRun, status_code=202)
async def start_entity_extraction(
    document_id: UUID, background_tasks: BackgroundTasks, response: Response,
    store: Store, entities: Entities, extractions: Annotated[SupabaseExtractionStore, Depends(get_extraction_store)],
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    extractor: Annotated[Callable, Depends(get_entity_extractor)],
    client_factory: Annotated[Callable, Depends(get_processing_client_factory)],
    schema: Annotated[Literal["core-v1", "research-v1", "full-v1"], Query()] = "full-v1",
) -> EntityRun:
    if not slots.acquire(blocking=False):
        raise HTTPException(409, "Another document is processing. Please try again shortly.")
    try:
        report = await get_extraction(document_id, store, extractions)
        run_id = uuid4()
        await entities.start(document_id, run_id)
        run = await entities.latest_run(document_id)
        if run is None or run.id != run_id:
            raise StorageUnavailable("Could not confirm entity extraction start")
        background_tasks.add_task(run_entity_attempt, document_id, run_id, report, schema,
            session, settings, extractor, client_factory)
        response.headers["Location"] = f"/documents/{document_id}/entities"
        return run
    except BaseException:
        slots.release()
        raise


@router.get("/{document_id}/entities", response_model=EntityPage)
async def list_entities(
    document_id: UUID, store: Store, entities: Entities,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    label: Annotated[str | None, Query(pattern=r"^[a-z]{3,20}$")] = None,
    status: Annotated[str | None, Query(pattern=r"^(suggested|accepted|rejected)$")] = None,
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> EntityPage:
    if await store.get(document_id) is None:
        raise HTTPException(404, "Document not found")
    return await entities.page(document_id, limit=limit, offset=offset,
        label=label, status=status, min_score=min_score)


@router.patch("/{document_id}/entities/{run_id}/{mention_id}", response_model=StoredMention)
async def review_entity(
    document_id: UUID, run_id: UUID, mention_id: UUID, body: ReviewRequest,
    store: Store, entities: Entities,
) -> StoredMention:
    if body.label is not None and body.status != "accepted":
        raise HTTPException(422, "Only an accepted mention can change type")
    if await store.get(document_id) is None:
        raise HTTPException(404, "Document not found")
    mention = await entities.review(run_id, mention_id, body.status, body.label)
    if mention.run_id != run_id or mention.document_id != document_id:
        raise StorageUnavailable("Invalid entity storage response")
    return mention
