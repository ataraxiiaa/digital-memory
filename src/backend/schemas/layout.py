"""Layout annotations kept separate from exact native-text citation regions."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .extraction import BoundingBox, ExtractionRecord, ExtractionRun


class LayoutRegion(ExtractionRecord):
    id: UUID
    source_label: str
    kind: Literal["text", "table", "figure", "caption", "equation", "unknown"]
    bbox: BoundingBox
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class LayoutPage(ExtractionRecord):
    page_number: int = Field(ge=1)
    status: Literal["detected", "needs_review", "failed"]
    regions: tuple[LayoutRegion, ...] = ()
    warnings: tuple[str, ...] = ()


class LayoutReport(ExtractionRecord):
    schema_version: Literal[1] = 1
    native_extraction: ExtractionRun
    detector: str
    model_repository: str
    model_revision: str
    tool_versions: dict[str, str]
    options: dict[str, str | bool | float]
    pages: tuple[LayoutPage, ...]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_page_inventory(self):
        if sorted(page.page_number for page in self.pages) != list(range(1, self.native_extraction.page_count + 1)):
            raise ValueError("Layout reports must account for every physical page exactly once")
        ids = [region.id for page in self.pages for region in page.regions]
        if len(ids) != len(set(ids)):
            raise ValueError("Layout region IDs must be unique")
        return self
