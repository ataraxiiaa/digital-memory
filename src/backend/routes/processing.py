"""Explicit PDF processing with persisted attempts and ownership-checked results."""

from hashlib import sha256
from datetime import datetime, timezone
from importlib.util import find_spec
import logging
from threading import BoundedSemaphore
from typing import Annotated, Callable
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.documents import Document
from ..schemas.layout import LayoutReport
from ..schemas.passages import PassagePage
from ..services.source_passages import passage_page
from ..services.pdf_extraction import PDFExtractionError
from ..storage.documents import StorageUnavailable, SupabaseDocumentStore
from ..storage.extractions import SupabaseExtractionStore
from .documents import Store, view_document_file


router = APIRouter(prefix="/documents", tags=["Processing"])
slots = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


def get_extraction_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseExtractionStore(client, settings, session.access_token.get_secret_value())


def get_pdf_processor() -> Callable:
    if any(find_spec(package) is None for package in ("docling", "onnxruntime")):
        raise HTTPException(503, "Document processing is unavailable. Please try again later.")
    from ..services.pdf_layout import detect_pdf_layout

    def process(data: bytes, *, document_id: UUID) -> LayoutReport:
        return detect_pdf_layout(data, document_id=document_id, include_tables=True, include_ocr=True)
    return process


def result_status(report: LayoutReport) -> str:
    if (report.warnings or report.native_extraction.status != "completed"
            or any(page.status != "detected" for page in report.pages)
            or report.tables
            or (report.ocr_extraction and report.ocr_extraction.status != "completed")):
        return "needs_review"
    return "ready"


Extractions = Annotated[SupabaseExtractionStore, Depends(get_extraction_store)]


def get_processing_client_factory():
    return lambda: httpx.AsyncClient(trust_env=False)


async def run_processing_attempt(document_id, run_id, session, settings, processor, client_factory):
    try:
        async with client_factory() as client:
            token = session.access_token.get_secret_value()
            store = SupabaseDocumentStore(client, url=settings.url,
                publishable_key=settings.publishable_key, access_token=token)
            extractions = SupabaseExtractionStore(client, settings, token)
            try:
                original = await view_document_file(document_id, store, session, settings, client)
                report = await run_in_threadpool(processor, original.body, document_id=document_id)
                if (report.native_extraction.document_id != document_id
                        or report.native_extraction.source_sha256 != sha256(original.body).hexdigest()):
                    raise PDFExtractionError("Extraction source mismatch")
                if report.native_extraction.status == "failed" and (
                    report.ocr_extraction is None or report.ocr_extraction.status == "failed"
                ):
                    raise PDFExtractionError("No extraction completed")
                # Report and status commit together. A late attempt cannot overwrite
                # a replacement attempt, or change a result after it committed.
                await extractions.finish(run_id, result_status(report), report)
            except Exception:
                try:
                    await extractions.finish(run_id, "failed")
                except (StorageUnavailable, HTTPException):
                    logger.warning("Processing outcome could not be persisted; refresh or retry after lease expiry")
    except Exception:
        logger.warning("Processing worker could not initialize; retry after lease expiry")
    finally:
        slots.release()


@router.post("/{document_id}/process", response_model=Document, status_code=202)
async def process_document(
    document_id: UUID, background_tasks: BackgroundTasks, response: Response,
    store: Store, extractions: Extractions,
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    processor: Annotated[Callable, Depends(get_pdf_processor)],
    client_factory: Annotated[Callable, Depends(get_processing_client_factory)],
) -> Document:
    if not slots.acquire(blocking=False):
        raise HTTPException(409, "Another document is processing. Please try again shortly.")
    try:
        document = await store.get(document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        if document.storage_path is None:
            raise HTTPException(422, "Only uploaded PDFs need extraction")
        run_id = await extractions.start(document_id)
        background_tasks.add_task(run_processing_attempt, document_id, run_id,
            session, settings, processor, client_factory)
        response.headers["Location"] = f"/documents/{document_id}"
        return document.model_copy(update={"status": "processing", "processing_run_id": run_id,
            "processing_started_at": datetime.now(timezone.utc), "processing_error": None})
    except BaseException:
        slots.release()
        raise


@router.get("/{document_id}/extraction", response_model=LayoutReport)
async def get_extraction(document_id: UUID, store: Store, extractions: Extractions) -> LayoutReport:
    document = await store.get(document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.processing_run_id is None:
        raise HTTPException(404, "This document has no extraction yet")
    report = await extractions.get_report(document.processing_run_id)
    if report is None:
        raise HTTPException(409, "The extraction is not available. Refresh the document status.")
    if report.native_extraction.document_id != document_id:
        raise StorageUnavailable("Stored extraction source does not match the document")
    return report


@router.get("/{document_id}/passages", response_model=PassagePage)
async def get_source_passages(
    document_id: UUID, store: Store, extractions: Extractions,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PassagePage:
    report = await get_extraction(document_id, store, extractions)
    try:
        return passage_page(report, limit=limit, offset=offset)
    except ValueError:
        raise StorageUnavailable("Saved extraction is not ready for source passages") from None
