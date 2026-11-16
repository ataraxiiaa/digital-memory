"""User-scoped, atomic processing transitions through ownership-checked RPCs."""

from uuid import UUID

import httpx
from fastapi import HTTPException

from ..auth.dependencies import unauthorized
from ..config import SupabaseSettings
from ..schemas.layout import LayoutReport
from .documents import StorageUnavailable


class SupabaseExtractionStore:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str):
        self.client = client
        self.base = f"{settings.url.rstrip('/')}/rest/v1"
        self.headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}

    async def request(self, method: str, path: str, **kwargs):
        try:
            response = await self.client.request(method, f"{self.base}/{path}",
                headers=self.headers, timeout=30, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise StorageUnavailable("Processing storage is unavailable") from None
        if response.status_code == 401:
            raise unauthorized()
        if not response.is_success:
            try:
                error = response.json()
                code = error.get("code") if isinstance(error, dict) else None
            except ValueError:
                code = None
            if code == "P0002":
                raise HTTPException(404, "Document not found")
            if code == "55000":
                raise HTTPException(409, "This document is already processing. Refresh its status.")
            raise StorageUnavailable("Processing storage request failed")
        try:
            return response.json()
        except ValueError:
            raise StorageUnavailable("Invalid processing storage response") from None

    async def start(self, document_id: UUID) -> UUID:
        result = await self.request("POST", "rpc/start_document_extraction", json={"p_document_id": str(document_id)})
        try:
            return UUID(result)
        except (ValueError, TypeError, AttributeError):
            raise StorageUnavailable("Could not confirm processing start; refresh the document") from None

    async def finish(self, run_id: UUID, status: str, report: LayoutReport | None = None) -> bool:
        result = await self.request("POST", "rpc/finish_document_extraction", json={
            "p_run_id": str(run_id), "p_status": status,
            "p_report": report.model_dump(mode="json") if report is not None else None,
        })
        if not isinstance(result, bool):
            raise StorageUnavailable("Could not confirm processing result; refresh the document")
        return result

    async def create_text(self, document_id: UUID, run_id: UUID, report: LayoutReport) -> UUID:
        """Idempotently attach a ready extraction to an owned text note."""
        result = await self.request("POST", "rpc/create_text_extraction", json={
            "p_document_id": str(document_id), "p_run_id": str(run_id),
            "p_report": report.model_dump(mode="json"),
        })
        try:
            return UUID(result)
        except (ValueError, TypeError, AttributeError):
            raise StorageUnavailable("Could not confirm the note extraction") from None

    async def get_report(self, run_id: UUID) -> LayoutReport | None:
        rows = await self.request("GET", "document_extractions", params={
            "id": f"eq.{run_id}", "select": "report", "limit": 1,
        })
        try:
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError("Unexpected response")
            return LayoutReport.model_validate(rows[0]["report"]) if rows and rows[0]["report"] else None
        except (ValueError, KeyError, TypeError):
            raise StorageUnavailable("Stored extraction could not be read") from None
