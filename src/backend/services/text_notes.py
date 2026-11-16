"""Adapter that turns an exact plain-text note into the extraction contract.

A note becomes a one-page, one-region extraction whose region text IS the note
text, character for character (including CRLF, Unicode, and trailing whitespace).
Citation offsets are therefore offsets into the note itself. Paragraph and
sentence boundaries are chosen later by `source_passages`, which already splits a
region into passages without overlap or normalization, so this adapter never
rewrites or splits the text. Page geometry is a placeholder: a note has no page.
"""

from datetime import datetime, timezone
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from ..schemas.documents import MAX_NOTE_CHARS
from ..schemas.extraction import ExtractionPage, ExtractionRegion, ExtractionRun
from ..schemas.layout import LayoutPage, LayoutReport

ADAPTER_VERSION = "text-note-v1"
_NAMESPACE = uuid5(NAMESPACE_URL, "https://digital-memory.invalid/text-note-extraction")


class NoteError(ValueError):
    """A note cannot be converted into reliable extraction records."""


def note_sha256(text: str) -> str:
    """Hash of the exact UTF-8 text; the database recomputes the same value."""
    try:
        return sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise NoteError("Notes must be valid Unicode text") from None


def build_note_report(text: str, *, document_id: UUID) -> LayoutReport:
    if not text.strip():
        raise NoteError("Notes must contain text")
    if "\x00" in text:
        raise NoteError("Notes must not contain null bytes")
    if len(text) > MAX_NOTE_CHARS:
        raise NoteError(f"Notes must not exceed {MAX_NOTE_CHARS:,} characters")
    digest = note_sha256(text)
    # IDs derive from the document and its exact text: identical text yields
    # identical IDs, and any edit yields new region IDs so review decisions made
    # on the old text can never attach to the new one.
    run_id = uuid5(_NAMESPACE, f"run|{document_id}|{digest}")
    region_id = uuid5(_NAMESPACE, f"region|{document_id}|{digest}")
    native = ExtractionRun(
        id=run_id, document_id=document_id, source_sha256=digest,
        created_at=datetime.now(timezone.utc), page_count=1, status="completed",
        tool_versions={"text_note_adapter": ADAPTER_VERSION},
        pages=(ExtractionPage(page_number=1, width_points=612, height_points=792, status="processed",
            regions=(ExtractionRegion(id=region_id, kind="text", status="extracted",
                method="native", text=text),)),),
    )
    return LayoutReport(native_extraction=native, detector=ADAPTER_VERSION,
        model_repository="none", model_revision="1", tool_versions={"text_note_adapter": ADAPTER_VERSION},
        options={}, pages=(LayoutPage(page_number=1, status="detected"),))
