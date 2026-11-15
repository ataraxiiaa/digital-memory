"""Document input and response schemas, independent of storage."""

from datetime import date, datetime
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    document_date: date | None = None
    collection: str = Field(default="Unfiled", min_length=1, max_length=100)
    synthetic: bool = False

    @field_validator("title", "collection")
    @classmethod
    def trim_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Label must not be blank")
        return value.strip()

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Content must be nonblank text without null bytes")
        return value


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    document_date: date | None
    collection: str
    synthetic: bool
    created_at: datetime
    status: Literal["ready", "uploaded", "processing", "needs_review", "failed"] = "ready"
    processing_run_id: UUID | None = None
    processing_started_at: datetime | None = None
    processing_error: str | None = None


class Document(DocumentSummary):
    content: str
    storage_path: str | None = None
    original_filename: str | None = None
    file_size_bytes: int | None = None
    page_count: int | None = None
