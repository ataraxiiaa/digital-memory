"""Keep recognized image text in a separate, citable extraction run."""

from datetime import datetime, timezone
from uuid import uuid4

from ..schemas.extraction import BoundingBox, ExtractionPage, ExtractionRegion, ExtractionRun


def map_ocr_extraction(native: ExtractionRun, detected_pages, tool_versions: dict[str, str]) -> ExtractionRun:
    by_number = {page.page_no: page for page in detected_pages}
    pages = []
    for source in native.pages:
        detected = by_number.get(source.page_number)
        regions = []
        warnings = []
        status = "processed"
        if (detected is None or detected.parsed_page is None or detected.size is None
                or detected.size.width <= 0 or detected.size.height <= 0):
            status = "failed"
            warnings.append("OCR page results are unavailable; inspect the source page.")
        else:
            width, height = detected.size.width, detected.size.height
            for cell in detected.parsed_page.textline_cells:
                if not cell.from_ocr:
                    continue
                try:
                    box = cell.rect.to_bounding_box().to_top_left_origin(page_height=height)
                    bbox = BoundingBox(x0=max(0, min(1, box.l / width)),
                        y0=max(0, min(1, box.t / height)), x1=max(0, min(1, box.r / width)),
                        y1=max(0, min(1, box.b / height)))
                    text = cell.orig
                    if not text.strip():
                        continue
                    notes = ["OCR text requires review against the original page."]
                    if "\ufffd" in text or "\x00" in text:
                        notes.append("Recognized text contains suspect characters; inspect the original page.")
                    if not any(char.isalnum() for char in text):
                        notes.append("OCR returned only symbols; this may be a graphic or recognition artifact.")
                    if cell.confidence < 0.8:
                        notes.append("OCR recognition confidence is below the initial 0.8 review threshold.")
                    regions.append(ExtractionRegion(id=uuid4(), kind="text", method="ocr",
                        status="needs_review", text=text, bbox=bbox, confidence=cell.confidence,
                        warnings=tuple(notes)))
                except ValueError:
                    warnings.append("An OCR line had invalid coordinates or confidence and was omitted.")
            if regions:
                warnings.append("Recognized image text requires review; native text remains a separate source.")
            elif not any(region.text.strip() for region in source.regions):
                warnings.append("No native or OCR text recovered; page may be blank, graphical, or unreadable.")
            if warnings:
                status = "needs_review"
        pages.append(ExtractionPage(page_number=source.page_number, printed_label=source.printed_label,
            width_points=source.width_points, height_points=source.height_points,
            status=status, regions=tuple(regions), warnings=tuple(dict.fromkeys(warnings))))
    status = "completed"
    if all(page.status == "failed" for page in pages):
        status = "failed"
    elif any(page.status != "processed" for page in pages):
        status = "completed_with_warnings"
    return ExtractionRun(id=uuid4(), document_id=native.document_id, source_sha256=native.source_sha256,
        created_at=datetime.now(timezone.utc), tool_versions=tool_versions,
        page_count=native.page_count, status=status, pages=tuple(pages))
