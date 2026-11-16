"""API contracts for canonical entities, merge suggestions, and merge history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .entities import EntityLabel
from .extraction import CitationReference

EntityStatus = Literal["suggested", "confirmed", "merged"]
SuggestionKind = Literal["exact", "initial", "surname", "related"]


class MentionEvidence(BaseModel):
    """One accepted mention that supports an entity, with its exact source citation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    run_id: UUID
    mention_id: UUID
    document_id: UUID
    page_number: int
    text: str
    citation: CitationReference


class EntityRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    type: EntityLabel
    canonical_name: str
    status: EntityStatus
    created_at: datetime
    mention_count: int = 0
    document_count: int = 0
    aliases: list[str] = []


class MergeRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    absorbed_id: UUID
    absorbed_name: str
    merged_at: datetime
    undone_at: datetime | None = None


class EntityDetail(BaseModel):
    entity: EntityRecord
    mentions: list[MentionEvidence]
    merges: list[MergeRecord]


class EntityList(BaseModel):
    total: int = Field(ge=0)
    offset: int
    limit: int
    entities: list[EntityRecord]


class SuggestionSide(BaseModel):
    id: UUID
    type: EntityLabel
    canonical_name: str
    status: EntityStatus
    mention_count: int
    aliases: list[str]
    evidence: list[MentionEvidence]


class MergeSuggestion(BaseModel):
    id: UUID
    kind: SuggestionKind
    score: float
    ambiguous: bool
    reason: str
    status: Literal["pending", "accepted", "dismissed"]
    a: SuggestionSide
    b: SuggestionSide


class SuggestionList(BaseModel):
    total: int = Field(ge=0)
    offset: int
    limit: int
    suggestions: list[MergeSuggestion]


class ResolveResult(BaseModel):
    relinked: int
    dropped_links: int
    removed_empty_entities: int
    new_entities: int
    new_links: int
    pending_suggestions: int
    skipped_ambiguous: int


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_id: UUID
    absorb_id: UUID


class ConfirmSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Which side survives; defaults to the entity with more mentions.
    keep_id: UUID | None = None


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)


class MentionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    mention_id: UUID
