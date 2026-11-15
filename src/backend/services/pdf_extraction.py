"""Native page text extraction and an explicit local-file command-line entrypoint."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
import pypdf
from pypdf import PdfReader

from ..schemas.extraction import ExtractionPage, ExtractionRegion, ExtractionRun
from .pdf_uploads import MAX_PDF_BYTES, inspect_pdf


class PDFExtractionError(ValueError):
    """A PDF cannot be processed into reliable page records."""


def extract_pdf_text(data: bytes, *, document_id: UUID) -> ExtractionRun:
    """Preserve native text as one region per page; empty pages require review."""
    if len(data) > MAX_PDF_BYTES:
        raise PDFExtractionError("PDF files must not exceed 30 MiB")
    try:
        page_count = inspect_pdf(data)
    except HTTPException as error:
        raise PDFExtractionError(str(error.detail)) from None

    reader = PdfReader(BytesIO(data), strict=True)
    pages = []
    for index in range(page_count):
        try:
            page = reader.pages[index]
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            if page.rotation % 180:
                width, height = height, width
        except Exception:
            raise PDFExtractionError(f"Cannot read geometry for page {index + 1}") from None

        warnings = ()
        text = ""
        try:
            # Preserve parser output, including whitespace, for stable citations.
            text = page.extract_text(extraction_mode="plain") or ""
            status = "processed"
            region_status = "extracted"
            if not text.strip():
                warnings = ("No native text extracted; inspect for blank or scanned content.",)
                status = "needs_review"
                region_status = "needs_review"
            elif "\ufffd" in text or "\x00" in text:
                warnings = ("Extracted text contains suspect characters; inspect the original page.",)
                status = "needs_review"
                region_status = "needs_review"
        except Exception:
            warnings = ("Native text extraction failed for this page.",)
            status = "failed"
            region_status = "failed"

        try:
            pages.append(ExtractionPage(
                page_number=index + 1, width_points=width, height_points=height,
                status=status, warnings=warnings, regions=(ExtractionRegion(
                    id=uuid4(), kind="text", status=region_status, method="native",
                    text=text, warnings=warnings,
                ),),
            ))
        except ValueError:
            raise PDFExtractionError(f"Invalid page geometry for page {index + 1}") from None

    run_status = "completed"
    if all(page.status == "failed" for page in pages):
        run_status = "failed"
    elif any(page.status != "processed" for page in pages):
        run_status = "completed_with_warnings"
    return ExtractionRun(
        id=uuid4(), document_id=document_id, source_sha256=sha256(data).hexdigest(),
        created_at=datetime.now(timezone.utc), page_count=page_count, status=run_status,
        tool_versions={"pypdf": pypdf.__version__, "native_text_adapter": "1", "extraction_mode": "plain"},
        pages=tuple(pages),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract native PDF page text into a local JSON record.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--document-id", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as source:
            data = source.read(MAX_PDF_BYTES + 1)
        result = extract_pdf_text(data, document_id=args.document_id)
        # Exclusive creation avoids replacing an earlier extraction and its citations.
        with args.output.open("x", encoding="utf-8") as target:
            target.write(result.model_dump_json(indent=2))
    except (OSError, PDFExtractionError) as error:
        parser.exit(1, f"Extraction failed: {error}\n")
    print(f"{result.status}: {result.page_count} pages. Saved to {args.output}")


if __name__ == "__main__":
    main()
