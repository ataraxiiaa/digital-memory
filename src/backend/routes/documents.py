"""Authenticated document creation and retrieval."""

from typing import Annotated
from uuid import UUID
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from ..auth.dependencies import (
    AuthenticatedSession, get_authenticated_session, get_auth_client,
    get_auth_settings, unauthorized,
)
from ..config import SupabaseSettings
from ..schemas.documents import Document, DocumentSummary, NewDocument
from ..storage.documents import DocumentStore, SupabaseDocumentStore
from ..services.pdf_uploads import upload_pdf
from ..services.pdf_uploads import MAX_PDF_BYTES


router = APIRouter(prefix="/documents", tags=["Documents"])


def get_document_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
) -> DocumentStore:
    # Reuse the request's HTTP client, but preserve the verified user's token.
    # Supabase RLS derives ownership; never use an administrative key here.
    return SupabaseDocumentStore(
        client, url=settings.url, publishable_key=settings.publishable_key,
        access_token=session.access_token.get_secret_value(),
    )


Store = Annotated[DocumentStore, Depends(get_document_store)]


@router.post("/upload", response_model=Document, status_code=201)
async def upload_document(
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
    file: Annotated[UploadFile, File(description="PDF up to 31,457,280 bytes (30 MiB)")],
    title: Annotated[str | None, Form(max_length=300)] = None,
    collection: Annotated[str, Form(min_length=1, max_length=100)] = "Unfiled",
) -> Document:
    """Store a private original PDF. Text extraction and OCR are not run yet."""
    document = await upload_pdf(
        file, title=title, collection=collection, user_id=session.user.id,
        access_token=session.access_token.get_secret_value(), settings=settings, client=client,
    )
    response.headers["Location"] = f"/documents/{document.id}"
    return document


@router.post("", response_model=Document, status_code=201)
async def create_document(document: NewDocument, response: Response, store: Store) -> Document:
    """Save a text document owned by the signed-in user."""
    result = await store.create(document)
    response.headers["Location"] = f"/documents/{result.id}"
    return result


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    store: Store,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentSummary]:
    """List the user's document metadata, newest first; omit full text."""
    return await store.list(limit=limit, offset=offset)


@router.get("/{document_id}", response_model=Document)
async def get_document(document_id: UUID, store: Store) -> Document:
    """Read an owned document; hidden and nonexistent documents both return 404."""
    document = await store.get(document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


@router.get("/{document_id}/file", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
async def view_document_file(
    document_id: UUID,
    store: Store,
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
) -> Response:
    """Return an owned PDF's original bytes without exposing a public URL."""
    document = await store.get(document_id)
    expected_path = f"{session.user.id}/{document_id}/original.pdf"
    if document is None or document.storage_path != expected_path:
        raise HTTPException(404, "Original PDF not found")
    headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {session.access_token.get_secret_value()}"}
    try:
        async with client.stream(
            "GET", f"{settings.url}/storage/v1/object/authenticated/documents/{expected_path}",
            headers=headers, timeout=60, follow_redirects=False,
        ) as result:
            if result.status_code == 401:
                raise unauthorized()
            if result.status_code in (400, 403, 404):
                raise HTTPException(404, "Original PDF not found")
            if not result.is_success:
                raise HTTPException(503, "PDF storage is unavailable")
            chunks = []
            size = 0
            async for chunk in result.aiter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise HTTPException(502, "Stored PDF exceeds the supported size")
                chunks.append(chunk)
    except httpx.RequestError:
        raise HTTPException(503, "PDF storage is unavailable") from None
    data = b"".join(chunks)
    if not data.startswith(b"%PDF-"):
        raise HTTPException(502, "Stored PDF could not be opened")
    filename = quote(document.original_filename or "document.pdf", safe="")
    return Response(
        data, media_type="application/pdf",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": f"inline; filename=\"document.pdf\"; filename*=UTF-8''{filename}"},
    )
