"""Layout annotations kept separate from exact native-text citation regions."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .extraction import BoundingBox, ExtractionRecord, ExtractionRun
from .tables import ExtractedTable


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
    tables: tuple[ExtractedTable, ...] = ()
    ocr_extraction: ExtractionRun | None = None

    @model_validator(mode="after")
    def validate_page_inventory(self):
        if sorted(page.page_number for page in self.pages) != list(range(1, self.native_extraction.page_count + 1)):
            raise ValueError("Layout reports must account for every physical page exactly once")
        ids = [region.id for page in self.pages for region in page.regions]
        if len(ids) != len(set(ids)):
            raise ValueError("Layout region IDs must be unique")
        if any(table.page_number > self.native_extraction.page_count for table in self.tables):
            raise ValueError("Table page references must belong to the source PDF")
        table_ids = [table.id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("Table IDs must be unique")
        if self.ocr_extraction is not None:
            native, ocr = self.native_extraction, self.ocr_extraction
            if (ocr.document_id != native.document_id or ocr.source_sha256 != native.source_sha256
                    or ocr.page_count != native.page_count or ocr.id == native.id):
                raise ValueError("OCR must be a separate extraction run for the same source document")
            if sorted(page.page_number for page in ocr.pages) != list(range(1, native.page_count + 1)):
                raise ValueError("OCR reports must account for every physical page")
            if any(region.method != "ocr" or region.bbox is None or region.confidence is None
                   for page in ocr.pages for region in page.regions):
                raise ValueError("OCR regions must record their method, coordinates, and recognition score")
        return self
