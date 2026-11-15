"""Merged cells, provenance, and failed table structures remain explicit."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.schemas.extraction import BoundingBox
from backend.schemas.tables import ExtractedTable, TableCell
from backend.services.pdf_tables import map_tables, render_table


def cell(text, row, col, *, row_end=None, col_end=None, header=False):
    return TableCell(text=text, row_start=row, row_end=row_end or row + 1,
        col_start=col, col_end=col_end or col + 1, column_header=header)


def table(cells, rows=2, cols=2):
    return ExtractedTable(id=uuid4(), source_ref="#/tables/0", page_number=1,
        bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9),
        num_rows=rows, num_cols=cols, cells=cells)


def document(cells, rows=2, cols=2):
    source_cells = [SimpleNamespace(text=c.text, start_row_offset_idx=c.row_start,
        end_row_offset_idx=c.row_end, start_col_offset_idx=c.col_start,
        end_col_offset_idx=c.col_end, column_header=c.column_header,
        row_header=c.row_header, row_section=c.row_section) for c in cells]
    box = SimpleNamespace(to_top_left_origin=lambda page_height:
        SimpleNamespace(l=10, t=20, r=90, b=180))
    item = SimpleNamespace(self_ref="#/tables/0", prov=[SimpleNamespace(page_no=1, bbox=box)],
        data=SimpleNamespace(num_rows=rows, num_cols=cols, table_cells=source_cells))
    return SimpleNamespace(tables=[item], pages={1: SimpleNamespace(size=SimpleNamespace(width=100, height=200))})


def test_merged_header_and_blank_cells_are_preserved():
    cells = [cell("Budget", 0, 0, col_end=2, header=True), cell("Travel", 1, 0), cell("", 1, 1)]
    results, warnings = map_tables(document(cells))
    assert not warnings
    result = results[0]
    assert result.page_number == 1
    assert result.bbox.x0 == 0.1
    assert result.cells == tuple(cells)
    assert result.status == "needs_review"
    html = render_table(result)
    assert 'colspan="2"' in html
    assert html.count("Budget") == 1
    assert "<th " in html
    assert "?</td>" not in html


def test_renderer_escapes_cell_text_and_marks_uncovered_positions():
    result = table([cell("<script>bad</script>", 0, 0)])
    html = render_table(result)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.count("Uncovered grid position") == 3
    assert "?</td>" not in html
    assert html.count('data-extraction-state="uncovered"') == 3
    results, _ = map_tables(document(result.cells))
    assert any("uncovered" in note for note in results[0].warnings)


@pytest.mark.parametrize("cells", [
    [cell("A", 0, 0, col_end=2), cell("B", 0, 1)],
    [cell("A", 0, 0, row_end=3)],
])
def test_invalid_grid_is_rejected_and_reported(cells):
    with pytest.raises(ValidationError):
        table(cells)
    results, warnings = map_tables(document(cells))
    assert not results
    assert "#/tables/0" in warnings[0]


def test_unsupported_multipage_table_is_reviewable():
    source = document([cell("A", 0, 0)])
    source.tables[0].prov *= 2
    results, warnings = map_tables(source)
    assert not results
    assert warnings


def test_empty_cell_text_warns_without_inventing_content():
    results, _ = map_tables(document([cell("", 0, 0)], rows=1, cols=1))
    assert results[0].cells[0].text == ""
    assert any("No table cell text" in warning for warning in results[0].warnings)
