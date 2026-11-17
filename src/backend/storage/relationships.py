"""User-scoped relationship reads and ownership-checked writes (RLS reads, security-definer RPCs)."""

from uuid import UUID

import httpx
from fastapi import HTTPException

from ..auth.dependencies import unauthorized
from ..config import SupabaseSettings
from ..schemas.extraction import CitationReference
from ..schemas.relationships import RelationDraft, RelationReview, Relationship
from ..services.relations import AcceptedMention
from .documents import StorageUnavailable

PAGE = 1000  # Supabase caps rows per request; larger sets are read page by page.
MAX_ROWS = 20000
CHUNK = 80  # ids per `in.(...)` filter, to keep request URLs short


class SupabaseRelationshipStore:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str):
        self.client = client
        self.base = f"{settings.url.rstrip('/')}/rest/v1"
        self.headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}

    async def _send(self, method: str, path: str, *, optional: bool = False, **kwargs):
        """`optional` treats a missing table as empty, for features from a later migration."""
        try:
            response = await self.client.request(method, f"{self.base}/{path}", headers=self.headers,
                timeout=30, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise StorageUnavailable("Relationship storage is unavailable") from None
        if response.status_code == 401:
            raise unauthorized()
        if optional and response.status_code == 404:
            return []
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
            raise StorageUnavailable("Relationship storage request failed")
        try:
            return response.json()
        except ValueError:
            raise StorageUnavailable("Invalid relationship storage response") from None

    async def _all(self, path: str, params: dict, **kwargs) -> list:
        rows: list = []
        while len(rows) < MAX_ROWS:
            page = await self._send("GET", path, params={**params, "limit": PAGE, "offset": len(rows)}, **kwargs)
            if not isinstance(page, list):
                raise StorageUnavailable("Invalid relationship storage response")
            rows.extend(page)
            if len(page) < PAGE:
                break
        return rows

    async def accepted_mentions(self, document_id: UUID) -> list[AcceptedMention]:
        """Accepted mentions from the document's latest finished entity run."""
        runs = await self._send("GET", "entity_runs", params={
            "document_id": f"eq.{document_id}", "status": "in.(completed,needs_review)",
            "select": "id", "order": "started_at.desc", "limit": 1})
        if not isinstance(runs, list):
            raise StorageUnavailable("Invalid relationship storage response")
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
            raise StorageUnavailable("Invalid relationship storage response") from None

    async def replace(self, document_id: UUID, drafts: tuple[RelationDraft, ...]) -> dict:
        result = await self._send("POST", "rpc/replace_relationships", json={
            "p_document_id": str(document_id), "p_items": [draft.model_dump(mode="json") for draft in drafts]})
        if not isinstance(result, dict):
            raise StorageUnavailable("Could not confirm the relationship update")
        return result

    async def list(self, *, document_id: UUID | None = None, status: str | None = None,
                   type_: str | None = None) -> list[Relationship]:
        params = {"select": "*", "order": "page_number.asc,sentence_start.asc,id.asc"}
        if document_id:
            params["document_id"] = f"eq.{document_id}"
        if status:
            params["review_status"] = f"eq.{status}"
        rows = await self._all("relationships", params)
        titles = await self._titles({row["document_id"] for row in rows})
        try:
            items = [Relationship.model_validate({**row, "document_title": titles.get(row["document_id"])}) for row in rows]
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable("Invalid relationship storage response") from None
        if type_:
            items = [item for item in items if (item.corrected_type or item.type) == type_]
        return items

    async def get(self, relationship_id: UUID) -> Relationship | None:
        rows = await self._send("GET", "relationships", params={"id": f"eq.{relationship_id}", "select": "*", "limit": 1})
        try:
            return Relationship.model_validate(rows[0]) if rows else None
        except (TypeError, ValueError, KeyError):
            raise StorageUnavailable("Invalid relationship storage response") from None

    async def review(self, relationship_id: UUID, review: RelationReview) -> Relationship:
        rows = await self._send("POST", "rpc/review_relationship", json={
            "p_id": str(relationship_id), "p_status": review.status, "p_type": review.type, "p_swap": review.swap})
        try:
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("Unexpected response")
            return Relationship.model_validate(rows[0])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid relationship storage response") from None

    async def _titles(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = await self._send("GET", "documents", params={
            "select": "id,title", "id": f"in.({','.join(sorted(ids))})", "limit": PAGE})
        return {row["id"]: row["title"] for row in rows} if isinstance(rows, list) else {}

    async def linked_mentions(self, entity_id: UUID) -> set[tuple[str, str]]:
        """(run_id, mention_id) pairs linked to one canonical entity (needs the identity migration)."""
        rows = await self._all("entity_links", {"entity_id": f"eq.{entity_id}", "select": "run_id,mention_id"}, optional=True)
        return {(row["run_id"], row["mention_id"]) for row in rows}

    async def entities_for_mentions(self, mention_ids: set[str]) -> tuple[dict[tuple[str, str], str], dict[str, dict]]:
        """Resolve mentions to canonical entities. Without the identity migration, nothing resolves."""
        ids = sorted(mention_ids)
        links: dict[tuple[str, str], str] = {}
        for index in range(0, len(ids), CHUNK):
            chunk = ids[index:index + CHUNK]
            rows = await self._send("GET", "entity_links", optional=True, params={
                "mention_id": f"in.({','.join(chunk)})", "select": "run_id,mention_id,entity_id", "limit": PAGE})
            for row in rows if isinstance(rows, list) else []:
                links[(row["run_id"], row["mention_id"])] = row["entity_id"]
        entity_ids = sorted(set(links.values()))
        entities: dict[str, dict] = {}
        for index in range(0, len(entity_ids), CHUNK):
            chunk = entity_ids[index:index + CHUNK]
            rows = await self._send("GET", "entities", optional=True, params={
                "id": f"in.({','.join(chunk)})", "select": "id,type,canonical_name,status", "limit": PAGE})
            for row in rows if isinstance(rows, list) else []:
                entities[row["id"]] = row
        return links, entities
