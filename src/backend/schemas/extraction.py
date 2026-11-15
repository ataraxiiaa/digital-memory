"""Versioned extraction records and exact references into preserved region text."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BoundingBox(ExtractionRecord):
    """Normalized coordinates on the displayed page, origin at top left."""

    x0: float = Field(ge=0, le=1, allow_inf_nan=False)
    y0: float = Field(ge=0, le=1, allow_inf_nan=False)
    x1: float = Field(ge=0, le=1, allow_inf_nan=False)
    y1: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_positive_area(self):
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Bounding boxes must have positive width and height")
        return self


class ExtractionRegion(ExtractionRecord):
    id: UUID
    kind: Literal["text", "table", "figure", "caption", "equation", "unknown"]
    status: Literal["extracted", "preserved", "needs_review", "failed"]
    method: Literal["native", "ocr", "table", "formula", "image", "unprocessed"]
    bbox: BoundingBox | None = None
    # This exact string is the basis of citation offsets. Never normalize in place.
    text: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    warnings: tuple[str, ...] = ()


class ExtractionPage(ExtractionRecord):
    page_number: int = Field(ge=1)
    printed_label: str | None = None
    width_points: float = Field(gt=0, allow_inf_nan=False)
    height_points: float = Field(gt=0, allow_inf_nan=False)
    status: Literal["pending", "processed", "needs_review", "failed"]
    regions: tuple[ExtractionRegion, ...] = ()
    warnings: tuple[str, ...] = ()


class CitationReference(ExtractionRecord):
    document_id: UUID
    extraction_id: UUID
    page_number: int = Field(ge=1)
    region_id: UUID
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def require_nonempty_span(self):
        if self.end_char <= self.start_char:
            raise ValueError("Citation end must follow its start")
        return self


class ExtractionRun(ExtractionRecord):
    schema_version: Literal[1] = 1
    id: UUID
    document_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    # Exact package/model/config identifiers used by this run.
    tool_versions: dict[str, str] = Field(min_length=1)
    page_count: int = Field(ge=1)
    status: Literal["queued", "processing", "completed", "completed_with_warnings", "failed"]
    pages: tuple[ExtractionPage, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_page_references(self):
        if self.created_at.utcoffset() is None:
            raise ValueError("Extraction timestamps must include a timezone")
        numbers = [page.page_number for page in self.pages]
        if len(numbers) != len(set(numbers)) or any(number > self.page_count for number in numbers):
            raise ValueError("Page numbers must be unique and within the source page count")
        region_ids = [region.id for page in self.pages for region in page.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("Region IDs must be unique within an extraction")
        if self.status in ("completed", "completed_with_warnings"):
            if len(self.pages) != self.page_count:
                raise ValueError("Completed extractions must account for every page")
            if any(page.status == "pending" for page in self.pages):
                raise ValueError("Completed extractions cannot contain pending pages")
        if self.status == "completed":
            if self.warnings or any(page.warnings or page.status != "processed" for page in self.pages):
                raise ValueError("Extraction issues require completed_with_warnings status")
            if any(region.warnings or region.status in ("needs_review", "failed")
                   for page in self.pages for region in page.regions):
                raise ValueError("Region issues require completed_with_warnings status")
        return self

    def resolve_citation(self, citation: CitationReference) -> str:
        """Resolve an end-exclusive Python string slice against this exact run."""
        if citation.document_id != self.document_id or citation.extraction_id != self.id:
            raise ValueError("Citation belongs to a different document or extraction")
        for page in self.pages:
            if page.page_number == citation.page_number:
                for region in page.regions:
                    if region.id == citation.region_id:
                        if citation.end_char > len(region.text):
                            raise ValueError("Citation extends beyond the preserved text")
                        return region.text[citation.start_char:citation.end_char]
        raise ValueError("Citation page or region does not exist")
