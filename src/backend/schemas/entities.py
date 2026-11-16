"""Reviewable entity mentions, not merged graph identities."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .extraction import CitationReference, ExtractionRecord


EntityLabel = Literal[
    "person", "organization", "project", "model", "method", "dataset", "component",
]


class EntityMention(ExtractionRecord):
    id: UUID
    passage_id: UUID
    label: EntityLabel
    text: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    citation: CitationReference
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: Literal["suggested"] = "suggested"
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_span(self):
        if (not self.text.strip() or self.end_char - self.start_char != len(self.text)
                or self.citation.end_char - self.citation.start_char != len(self.text)):
            raise ValueError("Mention text must match both span lengths")
        return self


class PassageEntities(ExtractionRecord):
    passage_id: UUID
    status: Literal["completed", "needs_review", "failed"]
    mentions: tuple[EntityMention, ...] = ()
    rejected_candidate_count: int = Field(default=0, ge=0)
    warnings: tuple[str, ...] = ()


class EntityReport(ExtractionRecord):
    document_id: UUID
    native_extraction_id: UUID
    ocr_extraction_id: UUID | None = None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: Literal["entity-mentions-v1"] = "entity-mentions-v1"
    label_schema_version: Literal["legacy", "core-v1", "research-v1", "custom"] = "legacy"
    model_repository: str
    model_revision: str
    library_version: str
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    labels: dict[str, str]
    chunk_size: int
    chunk_overlap: int
    device: Literal["cpu"] = "cpu"
    passages: tuple[PassageEntities, ...]
