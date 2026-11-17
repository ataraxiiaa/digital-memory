"""User-scoped timeline reads and ownership-checked writes (RLS reads, security-definer RPCs)."""

from uuid import UUID

import httpx
from fastapi import HTTPException

from ..auth.dependencies import unauthorized
from ..config import SupabaseSettings
from ..schemas.extraction import CitationReference
from ..schemas.timeline import TimelineItem, TimelineItemDraft, TimelineReview
from ..services.timeline import AcceptedMention
from .documents import StorageUnavailable

PAGE = 1000  # Supabase caps rows per request; larger sets are read page by page.
MAX_ROWS = 20000


class SupabaseTimelineStore:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str):
        self.client = client
        self.base = f"{settings.url.rstrip('/')}/rest/v1"
        self.headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}

    async def _json(self, method: str, path: str, **kwargs):
        try:
            response = await self.client.request(method, f"{self.base}/{path}", headers=self.headers,
                timeout=30, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise StorageUnavailable("Timeline storage is unavailable") from None
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
                raise HTTPException(422, "That change is not allowed.")
            raise StorageUnavailable("Timeline storage request failed")
        try:
            return response.json()
        except ValueError:
            raise StorageUnavailable("Invalid timeline storage response") from None

    async def _all(self, path: str, params: dict) -> list:
        rows: list = []
        while len(rows) < MAX_ROWS:
            page = await self._json("GET", path, params={**params, "limit": PAGE, "offset": len(rows)})
            if not isinstance(page, list):
                raise StorageUnavailable("Invalid timeline storage response")
            rows.extend(page)
            if len(page) < PAGE:
                break
        return rows

    async def accepted_mentions(self, document_id: UUID) -> list[AcceptedMention]:
        """Accepted mentions from the document's latest finished entity run."""
        runs = await self._json("GET", "entity_runs", params={
            "document_id": f"eq.{document_id}", "status": "in.(completed,needs_review)",
            "select": "id", "order": "started_at.desc", "limit": 1})
        if not isinstance(runs, list):
            raise StorageUnavailable("Invalid timeline storage response")
        if not runs:
            return []
        rows = await self._all("entity_mentions", {
            "run_id": f"eq.{runs[0]['id']}", "review_status": "eq.accepted",
            "select": "id,run_id,label,reviewed_label,text,citation", "order": "page_number.asc,start_char.asc,id.asc"})
        try:
            return [AcceptedMention(run_id=UUID(row["run_id"]), mention_id=UUID(row["id"]),
                label=row["reviewed_label"] or row["label"], text=row["text"],
                citation=CitationReference.model_validate(row["citation"])) for row in rows]
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable("Invalid timeline storage response") from None

    async def replace(self, document_id: UUID, items: tuple[TimelineItemDraft, ...]) -> dict:
        result = await self._json("POST", "rpc/replace_timeline_items", json={
            "p_document_id": str(document_id), "p_items": [item.model_dump(mode="json") for item in items]})
        if not isinstance(result, dict):
            raise StorageUnavailable("Could not confirm the timeline update")
        return result

    async def items(self, *, document_id: UUID | None = None, status: str | None = None) -> list[TimelineItem]:
        params = {"select": "*"}
        if document_id:
            params["document_id"] = f"eq.{document_id}"
        if status:
            params["review_status"] = f"eq.{status}"
        rows = await self._all("timeline_items", {**params, "order": "created_at.asc,id.asc"})
        titles = await self._titles({row["document_id"] for row in rows})
        try:
            return [TimelineItem.model_validate({**row, "document_title": titles.get(row["document_id"])}) for row in rows]
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable("Invalid timeline storage response") from None

    async def _titles(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = await self._json("GET", "documents", params={
            "select": "id,title", "id": f"in.({','.join(sorted(ids))})", "limit": PAGE})
        return {row["id"]: row["title"] for row in rows} if isinstance(rows, list) else {}

    async def linked_mentions(self, entity_id: UUID) -> set[tuple[str, str]]:
        """(run_id, mention_id) pairs linked to one canonical entity (needs the identity migration)."""
        rows = await self._all("entity_links", {"entity_id": f"eq.{entity_id}", "select": "run_id,mention_id"})
        return {(row["run_id"], row["mention_id"]) for row in rows}

    async def review(self, item_id: UUID, review: TimelineReview) -> TimelineItem:
        rows = await self._json("POST", "rpc/review_timeline_item", json={
            "p_id": str(item_id), "p_status": review.status, "p_title": review.title,
            "p_start": review.start.isoformat() if review.start else None,
            "p_end": review.end.isoformat() if review.end else None, "p_precision": review.precision})
        try:
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("Unexpected response")
            return TimelineItem.model_validate(rows[0])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid timeline storage response") from None
