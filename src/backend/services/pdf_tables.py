"""Adapt Docling table cells without flattening or filling blank cells."""

from html import escape
from uuid import uuid4

from ..schemas.extraction import BoundingBox
from ..schemas.tables import ExtractedTable, TableCell


def map_tables(document):
    tables = []
    warnings = []
    for item in document.tables:
        try:
            # This checkpoint handles single-page tables only.
            if len(item.prov) != 1:
                raise ValueError("Unsupported table provenance")
            provenance = item.prov[0]
            page = document.pages[provenance.page_no]
            box = provenance.bbox.to_top_left_origin(page_height=page.size.height)
            bbox = BoundingBox(x0=max(0, min(1, box.l / page.size.width)),
                y0=max(0, min(1, box.t / page.size.height)),
                x1=max(0, min(1, box.r / page.size.width)),
                y1=max(0, min(1, box.b / page.size.height)))
            cells = []
            for cell in item.data.table_cells:
                cell_bbox = None
                if getattr(cell, "bbox", None) is not None:
                    cb = cell.bbox.to_top_left_origin(page_height=page.size.height)
                    try:
                        cell_bbox = BoundingBox(x0=max(0, min(1, cb.l / page.size.width)),
                            y0=max(0, min(1, cb.t / page.size.height)),
                            x1=max(0, min(1, cb.r / page.size.width)),
                            y1=max(0, min(1, cb.b / page.size.height)))
                    except ValueError:
                        pass
                cells.append(TableCell(text=cell.text,
                row_start=cell.start_row_offset_idx, row_end=cell.end_row_offset_idx,
                col_start=cell.start_col_offset_idx, col_end=cell.end_col_offset_idx,
                column_header=cell.column_header, row_header=cell.row_header,
                row_section=cell.row_section, bbox=cell_bbox))
            notes = ["Model-extracted table structure requires review against the original page."]
            covered = sum((c.row_end-c.row_start)*(c.col_end-c.col_start) for c in cells)
            if covered < item.data.num_rows * item.data.num_cols:
                notes.append("The predicted grid has uncovered positions; content may be incomplete.")
            if not any(cell.text.strip() for cell in cells):
                notes.append("No table cell text was extracted; inspect for scanned content or extraction failure.")
            tables.append(ExtractedTable(id=uuid4(), source_ref=item.self_ref,
                page_number=provenance.page_no, bbox=bbox, num_rows=item.data.num_rows,
                num_cols=item.data.num_cols, cells=cells, warnings=tuple(notes)))
        except (ValueError, KeyError, ZeroDivisionError):
            warnings.append(f"Table {item.self_ref} could not be represented reliably; inspect its source region.")
    return tuple(tables), tuple(warnings)


def render_table(table: ExtractedTable) -> str:
    """Render each merged cell once; never invent text for empty grid positions."""
    starts = {(cell.row_start, cell.col_start): cell for cell in table.cells}
    covered = {(row, col) for cell in table.cells
        for row in range(cell.row_start, cell.row_end)
        for col in range(cell.col_start, cell.col_end)
        if (row, col) != (cell.row_start, cell.col_start)}
    rows = []
    for row in range(table.num_rows):
        values = []
        for col in range(table.num_cols):
            if (row, col) in covered:
                continue
            cell = starts.get((row, col))
            if cell is None:
                values.append('<td data-extraction-state="uncovered" title="Uncovered grid position"></td>')
                continue
            tag = "th" if cell.column_header or cell.row_header or cell.row_section else "td"
            content = escape(cell.text)
            if cell.text_runs:
                parts = []
                for run in cell.text_runs:
                    value = escape(run.text)
                    if run.script == "superscript":
                        value = f"<sup>{value}</sup>"
                    elif run.script == "subscript":
                        value = f"<sub>{value}</sub>"
                    parts.append(value)
                content = "".join(parts)
            values.append(f'<{tag} rowspan="{cell.row_end-cell.row_start}" colspan="{cell.col_end-cell.col_start}">{content}</{tag}>')
        rows.append("<tr>" + "".join(values) + "</tr>")
    notes = "".join(f"<li>{escape(warning)}</li>" for warning in table.warnings)
    return f'<h3>Table on page {table.page_number}</h3><ul>{notes}</ul><div class="table-scroll"><table>{"".join(rows)}</table></div>'
