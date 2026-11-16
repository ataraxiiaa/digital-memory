"""Prepare a plain-text note for entity extraction. Synchronous: no model is involved."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from ..schemas.documents import Document
from ..services.text_notes import NoteError, build_note_report
from ..storage.documents import StorageUnavailable
from .documents import Store
from .processing import Extractions

router = APIRouter(prefix="/documents", tags=["Text notes"])


@router.post("/{document_id}/process-text", response_model=Document)
async def prepare_text_note(document_id: UUID, store: Store, extractions: Extractions) -> Document:
    """Build the exact-text extraction for a note so Entities can run on it.

    Safe to repeat: the database keeps the existing extraction while the note's
    text is unchanged, so entity runs and review decisions are not disturbed.
    """
    document = await store.get(document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.storage_path is not None:
        raise HTTPException(422, "PDFs use the extraction action, not note preparation")
    try:
        report = build_note_report(document.content, document_id=document_id)
    except NoteError as error:
        raise HTTPException(422, str(error)) from None
    run_id = await extractions.create_text(document_id, uuid4(), report)
    if document.processing_run_id != run_id:
        # Re-read so the response reflects exactly what the database stored.
        document = await store.get(document_id)
        if document is None or document.processing_run_id != run_id:
            raise StorageUnavailable("Could not confirm the note extraction")
    return document
