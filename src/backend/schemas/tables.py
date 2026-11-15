"""Reviewable table structure with zero-based, end-exclusive cell spans."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .extraction import BoundingBox, ExtractionRecord


class TableTextRun(ExtractionRecord):
    text: str
    script: Literal["baseline", "superscript", "subscript"] = "baseline"


class TableCell(ExtractionRecord):
    text: str
    row_start: int = Field(ge=0)
    row_end: int = Field(gt=0)
    col_start: int = Field(ge=0)
    col_end: int = Field(gt=0)
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False
    bbox: BoundingBox | None = None
    text_runs: tuple[TableTextRun, ...] = ()
    script_detection: Literal["not_checked", "aligned", "unresolved"] = "not_checked"

    @model_validator(mode="after")
    def require_positive_span(self):
        if self.row_end <= self.row_start or self.col_end <= self.col_start:
            raise ValueError("Table cell spans must be positive")
        if self.text_runs and "".join(run.text for run in self.text_runs) != self.text:
            raise ValueError("Formatting runs must preserve the original cell text exactly")
        return self


class ExtractedTable(ExtractionRecord):
    id: UUID
    source_ref: str
    page_number: int = Field(ge=1)
    bbox: BoundingBox
    num_rows: int = Field(gt=0)
    num_cols: int = Field(gt=0)
    cells: tuple[TableCell, ...]
    status: Literal["needs_review"] = "needs_review"
    warnings: tuple[str, ...] = ("Model-extracted table structure requires review against the original page.",)

    @model_validator(mode="after")
    def validate_grid(self):
        occupied = set()
        for cell in self.cells:
            if cell.row_end > self.num_rows or cell.col_end > self.num_cols:
                raise ValueError("Cell extends beyond table dimensions")
            for row in range(cell.row_start, cell.row_end):
                for col in range(cell.col_start, cell.col_end):
                    if (row, col) in occupied:
                        raise ValueError("Table cells overlap in the logical grid")
                    occupied.add((row, col))
        return self
