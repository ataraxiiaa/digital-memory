"""Relationship suggestions between accepted mentions, each with exact evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extraction import CitationReference

RelationType = Literal["works_at", "located_in", "attended", "part_of", "member_of", "collaborated_with"]
ReviewStatus = Literal["suggested", "confirmed", "rejected"]


class RelationEnd(BaseModel):
    """One accepted mention taking part in a relationship."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    mention_id: UUID
    label: str
    text: str
    citation: CitationReference


class RelationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    document_id: UUID
    type: RelationType
    subject: RelationEnd
    object: RelationEnd
    page_number: int = Field(ge=1)
    # Exact slice of the preserved region text, with offsets into that region.
    sentence_text: str
    sentence_start: int = Field(ge=0)
    sentence_end: int = Field(gt=0)
    method: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_shape(self):
        if len(self.sentence_text) != self.sentence_end - self.sentence_start:
            raise ValueError("Sentence length must match its offsets")
        if self.subject.mention_id == self.object.mention_id:
            raise ValueError("A relationship needs two different mentions")
        return self


class RelationCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sentences_checked: int = 0
    filtered_negated: int = 0
    filtered_hedged: int = 0
    filtered_ended: int = 0
    filtered_cue: int = 0
    invalid_types: int = 0
    unmatched_spans: int = 0
    failed_sentences: int = 0


class RelationBuild(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[RelationDraft, ...]
    counts: RelationCounts


class Relationship(BaseModel):
    """Stored relationship including the reviewer's decision."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    document_id: UUID
    type: RelationType
    subject: RelationEnd
    object: RelationEnd
    page_number: int
    sentence_text: str
    sentence_start: int
    sentence_end: int
    method: str
    score: float
    review_status: ReviewStatus
    corrected_type: RelationType | None = None
    swapped: bool = False
    stale: bool = False
    reviewed_at: datetime | None = None
    document_title: str | None = None


class RelationshipPage(BaseModel):
    total: int
    offset: int
    limit: int
    relationships: list[Relationship]


class RelationReview(BaseModel):
    """Confirm or reject a suggestion, optionally with another type or swapped direction."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    type: RelationType | None = None
    swap: bool = False

    @model_validator(mode="after")
    def validate_correction(self):
        if self.status != "confirmed" and (self.type is not None or self.swap):
            raise ValueError("Only a confirmed relationship can be corrected")
        return self


class BuildResult(BaseModel):
    documents: int
    candidates: int
    filtered: int


class GraphNode(BaseModel):
    id: str
    name: str
    type: str
    entity_id: UUID | None = None
    mention_count: int = 0


class GraphEdge(BaseModel):
    id: str
    type: RelationType
    source: str
    target: str
    evidence_ids: list[UUID]
    count: int


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    relationship_count: int
