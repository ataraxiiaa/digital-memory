"""User-scoped search index writes and queries through owner-checked database functions."""

from uuid import UUID

import httpx
from fastapi import HTTPException

from ..auth.dependencies import unauthorized
from ..config import SupabaseSettings
from .documents import StorageUnavailable

PAGE = 1000


class SupabaseSearchStore:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str):
        self.client = client
        self.base = f"{settings.url.rstrip('/')}/rest/v1"
        self.headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}

    async def _json(self, method: str, path: str, **kwargs):
        try:
            response = await self.client.request(method, f"{self.base}/{path}", headers=self.headers,
                timeout=30, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise StorageUnavailable("Search storage is unavailable") from None
        if response.status_code == 401:
            raise unauthorized()
        if not response.is_success:
            try:
                error = response.json()
                code = error.get("code") if isinstance(error, dict) else None
            except ValueError:
                code = None
            if code == "P0002":
                raise HTTPException(404, "Not found")
            if code == "22023":
                raise HTTPException(422, "That search request is not allowed.")
            raise StorageUnavailable("Search storage request failed")
        try:
            return response.json()
        except ValueError:
            raise StorageUnavailable("Invalid search storage response") from None

    async def replace(self, document_id: UUID, extraction_id: UUID, rows: list[dict]) -> int:
        result = await self._json("POST", "rpc/replace_search_passages", json={
            "p_document_id": str(document_id), "p_extraction_id": str(extraction_id), "p_passages": rows})
        if not isinstance(result, dict) or not isinstance(result.get("passages"), int):
            raise StorageUnavailable("Could not confirm the search index update")
        return result["passages"]

    async def _rows(self, function: str, body: dict) -> list[dict]:
        rows = await self._json("POST", f"rpc/{function}", json=body)
        if not isinstance(rows, list):
            raise StorageUnavailable("Invalid search storage response")
        return rows

    async def passages(self, query: str, document_id: UUID | None, limit: int, offset: int) -> list[dict]:
        return await self._rows("search_passages_query", {"p_query": query,
            "p_document_id": str(document_id) if document_id else None, "p_limit": limit, "p_offset": offset})

    async def entities(self, query: str, limit: int, offset: int) -> list[dict]:
        return await self._rows("search_entities_query", {"p_query": query, "p_limit": limit, "p_offset": offset})

    async def timeline(self, query: str, limit: int, offset: int) -> list[dict]:
        return await self._rows("search_timeline_query", {"p_query": query, "p_limit": limit, "p_offset": offset})

    async def states(self) -> dict[str, dict]:
        """Index state per document id."""
        rows = await self._json("GET", "search_index_state", params={
            "select": "document_id,extraction_id,passage_count,indexed_at", "limit": PAGE})
        if not isinstance(rows, list):
            raise StorageUnavailable("Invalid search storage response")
        return {row["document_id"]: row for row in rows}

    async def titles(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = await self._json("GET", "documents", params={
            "select": "id,title", "id": f"in.({','.join(sorted(ids))})", "limit": PAGE})
        return {row["id"]: row["title"] for row in rows} if isinstance(rows, list) else {}
