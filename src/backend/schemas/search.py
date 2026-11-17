"""API contracts for keyword search. Snippets are segments, never markup."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .extraction import CitationReference

SearchType = Literal["passages", "entities", "timeline", "all"]
HitKind = Literal["passage", "entity", "mention", "timeline"]


class SnippetSegment(BaseModel):
    """A run of source text; `match` marks text that matched the query."""

    model_config = ConfigDict(frozen=True)

    text: str
    match: bool = False


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: HitKind
    score: float = 0
    snippet: list[SnippetSegment]
    document_id: UUID | None = None
    document_title: str | None = None
    page_number: int | None = None
    # Present whenever the hit points into a document: the exact source span.
    citation: CitationReference | None = None
    review_required: bool | None = None
    # entity / mention fields
    entity_id: UUID | None = None
    mention_id: UUID | None = None
    run_id: UUID | None = None
    entity_type: str | None = None
    entity_status: str | None = None
    mention_count: int | None = None
    # timeline fields
    timeline_item_id: UUID | None = None
    date_text: str | None = None
    start_date: date | None = None
    review_status: str | None = None


class SearchResponse(BaseModel):
    query: str
    type: SearchType
    # Matches per kind. For type=all, each kind is paged with the same limit and offset.
    totals: dict[str, int]
    offset: int
    limit: int
    results: list[SearchHit]


class IndexResult(BaseModel):
    documents: int
    passages: int
    skipped: int = 0


class DocumentIndexStatus(BaseModel):
    document_id: UUID
    title: str
    status: Literal["current", "stale", "missing", "not_ready"]
    passage_count: int = 0
    indexed_at: datetime | None = None


class IndexStatus(BaseModel):
    documents: list[DocumentIndexStatus]
    current: int = Field(ge=0)
    stale: int = Field(ge=0)
    missing: int = Field(ge=0)
    not_ready: int = Field(ge=0)
