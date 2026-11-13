"""Bounded PDF validation and private original-file persistence."""

import logging
from io import BytesIO
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from ..config import SupabaseSettings
from ..schemas.documents import Document


MAX_PDF_BYTES = 30 * 1024 * 1024  # Matches the live bucket's 30 MiB limit.
MAX_PDF_PAGES = 1000
logger = logging.getLogger(__name__)


def inspect_pdf(data: bytes) -> int:
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "The file is not a valid PDF")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise HTTPException(422, "Password-protected PDFs are not supported")
        count = len(reader.pages)
        if count < 1:
            raise HTTPException(422, "The PDF has no pages")
        if count > MAX_PDF_PAGES:
            raise HTTPException(422, f"PDFs must have at most {MAX_PDF_PAGES} pages")
        return count
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "The PDF is damaged or cannot be opened") from None


async def read_pdf(file: UploadFile) -> tuple[bytes, str, int]:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".pdf") or file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(415, "Upload a PDF file with a .pdf filename")
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "The PDF filename is invalid or too long")
    chunks = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF files must not exceed 30 MiB (31,457,280 bytes)")
        chunks.append(chunk)
    if not size:
        raise HTTPException(422, "The uploaded file is empty")
    data = b"".join(chunks)
    pages = await run_in_threadpool(inspect_pdf, data)
    return data, filename, pages


async def upload_pdf(
    file: UploadFile, *, title: str | None, collection: str,
    user_id: UUID, access_token: str, settings: SupabaseSettings,
    client: httpx.AsyncClient,
) -> Document:
    try:
        data, filename, pages = await read_pdf(file)
    finally:
        await file.close()
    label = title.strip() if title is not None else filename[:-4].strip()
    collection = collection.strip()
    if not 1 <= len(label) <= 300 or "\x00" in label:
        raise HTTPException(422, "Title must contain 1 to 300 characters")
    if not 1 <= len(collection) <= 100 or "\x00" in collection:
        raise HTTPException(422, "Collection must contain 1 to 100 characters")
    document_id = uuid4()
    path = f"{user_id}/{document_id}/original.pdf"
    headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {access_token}"}
    storage_url = f"{settings.url}/storage/v1/object/documents/{path}"
    try:
        uploaded = await client.post(storage_url, content=data, headers={**headers, "Content-Type": "application/pdf", "x-upsert": "false"}, timeout=60, follow_redirects=False)
    except httpx.RequestError:
        # Outcome may be unknown after a timeout; attempt safe path-specific cleanup.
        await cleanup_file(client, settings, headers, path)
        raise HTTPException(503, "PDF storage is unavailable; please try again") from None
    if not uploaded.is_success:
        raise HTTPException(503, "PDF upload failed; check the private bucket and ownership policies")
    payload = {
        "id": str(document_id), "title": label, "content": "", "collection": collection,
        "status": "uploaded", "synthetic": False, "storage_path": path,
        "original_filename": filename, "file_size_bytes": len(data), "page_count": pages,
    }
    try:
        saved = await client.post(f"{settings.url}/rest/v1/documents", json=payload, headers={**headers, "Prefer": "return=representation"}, timeout=30, follow_redirects=False)
        if not saved.is_success:
            raise ValueError("Database rejected metadata")
        rows = saved.json()
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("Invalid metadata response")
        return Document.model_validate(rows[0])
    except (httpx.RequestError, ValueError):
        # The delete policy refuses removal if a row committed despite a timeout.
        await cleanup_file(client, settings, headers, path)
        raise HTTPException(503, "Could not confirm the saved PDF. Refresh your document list before retrying.") from None


async def cleanup_file(client: httpx.AsyncClient, settings: SupabaseSettings, headers: dict, path: str) -> None:
    try:
        result = await client.request("DELETE", f"{settings.url}/storage/v1/object/documents", json={"prefixes": [path]}, headers=headers, timeout=15, follow_redirects=False)
        if not result.is_success:
            logger.warning("PDF cleanup could not be confirmed; administrative reconciliation may be needed")
    except httpx.RequestError:
        logger.warning("PDF cleanup could not reach storage; administrative reconciliation may be needed")
