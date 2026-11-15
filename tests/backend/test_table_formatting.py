"""Glyph baselines distinguish powers from ordinary digits without changing text."""

from io import BytesIO
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from backend.schemas.extraction import BoundingBox
from backend.schemas.tables import ExtractedTable, TableCell, TableTextRun
from backend.services.pdf_table_formatting import Glyph, infer_script_runs, preserve_table_scripts
from backend.services.pdf_tables import render_table


def glyph(text, x, y=100, size=10):
    return Glyph(text, x, y, size, x, y, x + 5, y + size)


def test_detects_superscript_and_preserves_whitespace():
    runs = infer_script_runs("n 2", [glyph("n", 10), glyph("2", 16, 104, 7)])
    assert "".join(run.text for run in runs) == "n 2"
    assert runs[-1] == TableTextRun(text="2", script="superscript")


def test_digits_on_baseline_are_not_powers():
    assert infer_script_runs("1020", [glyph(c, i*6) for i, c in enumerate("1020")]) == (
        TableTextRun(text="1020"),)


def test_detects_subscript_and_multidigit_exponent():
    runs = infer_script_runs("dk", [glyph("d", 10), glyph("k", 16, 98, 7)])
    assert runs[-1].script == "subscript"
    runs = infer_script_runs("1020", [glyph("1", 0), glyph("0", 6),
        glyph("2", 12, 104, 7), glyph("0", 16, 104, 7)])
    assert runs[-1] == TableTextRun(text="20", script="superscript")


def test_mismatched_text_is_not_reformatted():
    assert infer_script_runs("28", [glyph("2", 0), glyph("9", 6)]) is None
    assert infer_script_runs("28", [glyph("2", 0)]) is None


def test_runs_cannot_replace_original_cell_text():
    with pytest.raises(ValidationError, match="original cell text"):
        TableCell(text="n2", row_start=0, row_end=1, col_start=0, col_end=1,
            text_runs=[TableTextRun(text="n3")])


def test_native_pdf_geometry_renders_power_without_changing_cell_text():
    pytest.importorskip("pypdfium2")
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 1 0 0 1 50 100 Tm (n) Tj /F1 8 Tf 1 0 0 1 57 105 Tm (2) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    data = BytesIO()
    writer.write(data)
    table = ExtractedTable(id=uuid4(), source_ref="#/tables/0", page_number=1,
        bbox=BoundingBox(x0=.2, y0=.3, x1=.5, y1=.6), num_rows=1, num_cols=1,
        cells=[TableCell(text="n2", row_start=0, row_end=1, col_start=0, col_end=1,
            bbox=BoundingBox(x0=.2, y0=.3, x1=.5, y1=.6))])
    result = preserve_table_scripts(data.getvalue(), [table])[0]
    assert result.cells[0].text == "n2"
    assert result.cells[0].script_detection == "aligned"
    assert "n<sup>2</sup>" in render_table(result)
    assert not table.cells[0].text_runs
