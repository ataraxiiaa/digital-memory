"""Recover table script formatting from native glyph geometry, with exact text alignment."""

import ctypes
from dataclasses import dataclass

from ..schemas.tables import TableTextRun


@dataclass(frozen=True)
class Glyph:
    text: str
    x: float
    y: float
    font_size: float
    left: float
    bottom: float
    right: float
    top: float


def infer_script_runs(text: str, glyphs: list[Glyph]) -> tuple[TableTextRun, ...] | None:
    """Return None when source glyphs cannot safely align to preserved cell text."""
    visible = [glyph for glyph in glyphs if glyph.text and not glyph.text.isspace()]
    characters = [char for char in text if not char.isspace()]
    if len(visible) != len(characters):
        return None
    if any(glyph.text != char and glyph.text != "\ufffd" for glyph, char in zip(visible, characters)):
        return None
    if not visible:
        return ()
    size = max(glyph.font_size for glyph in visible)
    baseline = [glyph for glyph in visible if glyph.font_size >= size * 0.9]
    scripts = []
    for glyph in visible:
        script = "baseline"
        if 0 < glyph.font_size < size * 0.85:
            nearby = [base for base in baseline
                if abs(base.x - glyph.x) <= size * 3 and abs(base.y - glyph.y) < size * 0.8]
            if nearby:
                base = min(nearby, key=lambda candidate: abs(candidate.y-glyph.y)*2 + abs(candidate.x-glyph.x)*0.1)
                shift = glyph.y - base.y
                if shift > size * 0.15:
                    script = "superscript"
                elif shift < -size * 0.1:
                    script = "subscript"
        scripts.append(script)
    runs = []
    index = 0
    for char in text:
        script = "baseline"
        if not char.isspace():
            script = scripts[index]
            index += 1
        if runs and runs[-1].script == script:
            previous = runs.pop()
            runs.append(TableTextRun(text=previous.text + char, script=script))
        else:
            runs.append(TableTextRun(text=char, script=script))
    return tuple(runs)


def preserve_table_scripts(data: bytes, tables):
    """Add formatting to separate table text; native extraction stays unchanged."""
    import pypdfium2
    from pypdfium2 import raw

    updated = []
    with pypdfium2.PdfDocument(data) as pdf:
        for table in tables:
            page = pdf[table.page_number - 1]
            textpage = None
            try:
                width, height = page.get_size()
                glyphs = []
                if page.get_rotation() == 0:
                    textpage = page.get_textpage()
                    for index in range(textpage.count_chars()):
                        text = textpage.get_text_range(index, 1)
                        if not text or text.isspace():
                            continue
                        x, y = ctypes.c_double(), ctypes.c_double()
                        raw.FPDFText_GetCharOrigin(textpage.raw, index, x, y)
                        box = textpage.get_charbox(index)
                        glyphs.append(Glyph(text, x.value, y.value,
                            raw.FPDFText_GetFontSize(textpage.raw, index), *box))
                cells = []
                for cell in table.cells:
                    runs = None
                    if cell.bbox is not None and glyphs:
                        b = cell.bbox
                        selected = [g for g in glyphs
                            if b.x0*width-0.5 <= (g.left+g.right)/2 <= b.x1*width+0.5
                            and (1-b.y1)*height-0.5 <= (g.bottom+g.top)/2 <= (1-b.y0)*height+0.5]
                        runs = infer_script_runs(cell.text, selected)
                    cells.append(cell.model_copy(update={"text_runs": runs or (),
                        "script_detection": "aligned" if runs is not None else "unresolved"}))
                warnings = list(table.warnings)
                if any(cell.script_detection == "unresolved" and cell.text.strip() for cell in cells):
                    warnings.append("Script formatting could not be verified for some cells; inspect the original page.")
                updated.append(table.model_copy(update={"cells": tuple(cells), "warnings": tuple(warnings)}))
            finally:
                if textpage is not None:
                    textpage.close()
                page.close()
    return tuple(updated)
