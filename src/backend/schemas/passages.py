"""Exact source text prepared for evidence-backed entity extraction."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .extraction import CitationReference, ExtractionRecord


class SourcePassage(ExtractionRecord):
    id: UUID
    ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)
    citation: CitationReference
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: Literal["native", "ocr"]
    review_required: bool
    warnings: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_text_span(self):
        if not self.text.strip():
            raise ValueError("Passages must contain non-whitespace text")
        if len(self.text) != self.citation.end_char - self.citation.start_char:
            raise ValueError("Passage length must match the exact source range")
        return self


class PassagePage(ExtractionRecord):
    document_id: UUID
    native_extraction_id: UUID
    ocr_extraction_id: UUID | None = None
    algorithm_version: Literal["source-passages-v1"] = "source-passages-v1"
    max_chars: int = Field(gt=0)
    total: int = Field(ge=0)
    skipped_region_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    passages: tuple[SourcePassage, ...]
