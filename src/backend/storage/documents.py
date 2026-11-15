"""Document contract and Supabase Data API storage boundary.

Authenticated routes use this boundary. Never accept an
owner ID from a request: the database derives ownership from the user's token.
"""

from typing import Protocol
from uuid import UUID

import httpx
from backend.schemas.documents import Document, DocumentSummary, NewDocument


class StorageUnavailable(Exception):
    """Safe error boundary: do not expose upstream bodies or credentials."""


class DocumentStore(Protocol):
    async def create(self, document: NewDocument) -> Document: ...
    async def list(self, *, limit: int = 20, offset: int = 0) -> list[DocumentSummary]: ...
    async def get(self, document_id: UUID) -> Document | None: ...


class SupabaseDocumentStore:
    """Request-scoped adapter using a publishable key and a user's access token.

The caller owns and closes the HTTP client. RLS authorizes every database call.
Do not construct this with a secret/service-role key or reuse across users.
"""

    SUMMARY_COLUMNS = "id,title,document_date,collection,synthetic,created_at,status,processing_run_id,processing_started_at,processing_error"

    def __init__(
        self, client: httpx.AsyncClient, *, url: str,
        publishable_key: str, access_token: str,
    ) -> None:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Supabase URL must be an HTTPS origin")
        if not publishable_key.startswith("sb_publishable_"):
            raise ValueError("Use a Supabase publishable key, never a secret key")
        if not access_token.strip() or any(c in access_token for c in "\r\n"):
            raise ValueError("A user access token is required")
        self._client = client
        self._url = f"{str(parsed).rstrip('/')}/rest/v1/documents"
        self._headers = {"apikey": publishable_key, "Authorization": f"Bearer {access_token}"}

    async def _request(self, method: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(
                method, self._url, headers=self._headers, timeout=30,
                follow_redirects=False, **kwargs,
            )
        except httpx.RequestError:
            raise StorageUnavailable("Document storage is unavailable") from None
        if not response.is_success:
            raise StorageUnavailable("Document storage request failed")
        return response

    async def create(self, document: NewDocument) -> Document:
        # PostgREST returns metadata only by default; request a representation.
        try:
            response = await self._client.post(
                self._url, headers={**self._headers, "Prefer": "return=representation"},
                json=document.model_dump(mode="json"), timeout=30,
                follow_redirects=False,
            )
            if not response.is_success:
                raise StorageUnavailable("Document storage request failed")
            rows = response.json()
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("Unexpected response")
            return Document.model_validate(rows[0])
        except httpx.RequestError:
            raise StorageUnavailable("Document storage is unavailable") from None
        except (ValueError, TypeError, KeyError):
            raise StorageUnavailable("Invalid document storage response") from None

    async def list(self, *, limit: int = 20, offset: int = 0) -> list[DocumentSummary]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Limit must be 1–100 and offset must be nonnegative")
        response = await self._request("GET", params={
            "select": self.SUMMARY_COLUMNS, "order": "created_at.desc,id.desc",
            "limit": limit, "offset": offset,
        })
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("Unexpected response")
            return [DocumentSummary.model_validate(row) for row in rows]
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid document storage response") from None

    async def get(self, document_id: UUID) -> Document | None:
        response = await self._request("GET", params={"select": f"{self.SUMMARY_COLUMNS},content,storage_path,original_filename,file_size_bytes,page_count", "id": f"eq.{document_id}", "limit": 1})
        try:
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError("Unexpected response")
            return Document.model_validate(rows[0]) if rows else None
        except (ValueError, TypeError, KeyError):
            raise StorageUnavailable("Invalid document storage response") from None
