"""User-scoped entity runs and mentions through ownership-checked RPCs and RLS reads."""

from collections import Counter
from uuid import UUID

import httpx
from fastapi import HTTPException

from ..auth.dependencies import unauthorized
from ..config import SupabaseSettings
from ..schemas.entities import EntityReport
from ..schemas.entity_review import EntityCounts, EntityPage, EntityRun, StoredMention
from .documents import StorageUnavailable

MAX_COUNTED_ROWS = 20000


class SupabaseEntityStore:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str):
        self.client = client
        self.base = f"{settings.url.rstrip('/')}/rest/v1"
        self.headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        extra = kwargs.pop("headers", {})
        try:
            response = await self.client.request(method, f"{self.base}/{path}",
                headers={**self.headers, **extra}, timeout=30, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise StorageUnavailable("Entity storage is unavailable") from None
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
            if code == "55000":
                raise HTTPException(409, "Entity extraction is already running for this document.")
            raise StorageUnavailable("Entity storage request failed")
        return response

    async def _json(self, method: str, path: str, **kwargs):
        response = await self._send(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise StorageUnavailable("Invalid entity storage response") from None

    async def start(self, document_id: UUID, run_id: UUID) -> None:
        await self._send("POST", "rpc/start_entity_run",
            json={"p_document_id": str(document_id), "p_run_id": str(run_id)})

    async def finish(self, run_id: UUID, status: str, report: EntityReport | None = None) -> bool:
        payload = {"p_run_id": str(run_id), "p_status": status, "p_meta": None, "p_mentions": []}
        if report is not None:
            mentions = [mention for passage in report.passages for mention in passage.mentions]
            payload["p_meta"] = {
                "label_schema_version": report.label_schema_version,
                "model_repository": report.model_repository,
                "model_revision": report.model_revision,
                "library_version": report.library_version,
                "threshold": report.threshold, "labels": report.labels,
                "passage_count": len(report.passages),
                "failed_passage_count": sum(p.status == "failed" for p in report.passages),
                "rejected_candidate_count": sum(p.rejected_candidate_count for p in report.passages),
            }
            payload["p_mentions"] = [m.model_dump(mode="json", include={
                "id", "passage_id", "text", "label", "score", "source_sha256", "citation", "warnings",
            }) for m in mentions]
        result = await self._json("POST", "rpc/finish_entity_run", json=payload)
        if not isinstance(result, bool):
            raise StorageUnavailable("Could not confirm entity result")
        return result

    async def latest_run(self, document_id: UUID) -> EntityRun | None:
        rows = await self._json("GET", "entity_runs", params={
            "document_id": f"eq.{document_id}", "select": "*",
            "order": "started_at.desc", "limit": 1})
        try:
            return EntityRun.model_validate(rows[0]) if rows else None
        except (ValueError, TypeError, KeyError, IndexError):
            raise StorageUnavailable("Invalid entity storage response") from None

    async def page(self, document_id: UUID, *, limit: int, offset: int, label: str | None,
                   status: str | None, min_score: float | None) -> EntityPage:
        run = await self.latest_run(document_id)
        if run is None:
            return EntityPage(run=None, counts=EntityCounts(), total=0, offset=offset, limit=limit, mentions=[])
        base = {"run_id": f"eq.{run.id}"}
        # Counts cover the whole run, independent of the page filters.
        rows = await self._json("GET", "entity_mentions", params={
            **base, "select": "label,review_status,reviewed_label", "limit": MAX_COUNTED_ROWS})
        try:
            by_status = Counter(row["review_status"] for row in rows)
            by_label = Counter(row["reviewed_label"] or row["label"] for row in rows
                if row["review_status"] != "rejected")
        except (TypeError, KeyError):
            raise StorageUnavailable("Invalid entity storage response") from None
        counts = EntityCounts(total=len(rows), suggested=by_status["suggested"],
            accepted=by_status["accepted"], rejected=by_status["rejected"], by_label=dict(by_label))
        params = {**base, "select": "*", "order": "page_number.asc,start_char.asc,label.asc",
            "limit": limit, "offset": offset}
        if label:
            params["label"] = f"eq.{label}"
        if status:
            params["review_status"] = f"eq.{status}"
        if min_score is not None:
            params["score"] = f"gte.{min_score}"
        response = await self._send("GET", "entity_mentions", params=params, headers={"Prefer": "count=exact"})
        try:
            mentions = [StoredMention.model_validate(row) for row in response.json()]
            total = int(response.headers.get("content-range", "").rpartition("/")[2])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid entity storage response") from None
        return EntityPage(run=run, counts=counts, total=total, offset=offset, limit=limit, mentions=mentions)

    async def review(self, run_id: UUID, mention_id: UUID, status: str, label: str | None) -> StoredMention:
        rows = await self._json("POST", "rpc/review_entity_mention", json={
            "p_run_id": str(run_id), "p_mention_id": str(mention_id),
            "p_status": status, "p_label": label})
        try:
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("Unexpected response")
            return StoredMention.model_validate(rows[0])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid entity storage response") from None
