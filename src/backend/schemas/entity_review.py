"""API contracts for stored entity suggestions and their human review."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .entities import EntityLabel
from .extraction import CitationReference

ReviewStatus = Literal["suggested", "accepted", "rejected"]
RunStatus = Literal["processing", "completed", "needs_review", "failed"]


class StoredMention(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    run_id: UUID
    document_id: UUID
    passage_id: UUID
    page_number: int
    label: EntityLabel
    text: str
    start_char: int
    end_char: int
    score: float
    citation: CitationReference
    warnings: list[str] = []
    review_status: ReviewStatus
    reviewed_label: EntityLabel | None = None
    reviewed_at: datetime | None = None


class EntityRun(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    document_id: UUID
    extraction_id: UUID
    status: RunStatus
    label_schema_version: str | None = None
    model_revision: str | None = None
    threshold: float | None = None
    passage_count: int | None = None
    failed_passage_count: int | None = None
    rejected_candidate_count: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class EntityCounts(BaseModel):
    total: int = 0
    suggested: int = 0
    accepted: int = 0
    rejected: int = 0
    by_label: dict[str, int] = {}


class EntityPage(BaseModel):
    run: EntityRun | None
    counts: EntityCounts
    total: int = Field(ge=0, description="Mentions matching the current filters")
    offset: int
    limit: int
    mentions: list[StoredMention]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    # Optional corrected type; only meaningful when accepting.
    label: EntityLabel | None = None
