"""Local Docling layout analysis with native text and a visual review report."""

import argparse
import base64
from collections import Counter
from html import escape
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from ..schemas.extraction import BoundingBox, ExtractionRun
from ..schemas.layout import LayoutPage, LayoutRegion, LayoutReport
from .pdf_extraction import PDFExtractionError, extract_pdf_text
from .pdf_uploads import MAX_PDF_BYTES


MODEL_REPOSITORY = "docling-project/docling-layout-heron"
MODEL_REVISION = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
TEXT_LABELS = {"text", "title", "section_header", "list_item", "page_header", "page_footer", "footnote", "reference", "code", "paragraph"}
SPECIAL_LABELS = {"table": "table", "picture": "figure", "chart": "figure", "formula": "equation", "caption": "caption"}


def map_layout_pages(extraction: ExtractionRun, detected_pages) -> tuple[LayoutPage, ...]:
    """Convert Docling boxes to normalized top-left coordinates on each page."""
    by_number = {page.page_no: page for page in detected_pages}
    pages = []
    for source in extraction.pages:
        detected = by_number.get(source.page_number)
        regions = []
        warnings = list(source.warnings)
        if (detected is None or detected.size is None or detected.predictions.layout is None
                or detected.size.width <= 0 or detected.size.height <= 0):
            pages.append(LayoutPage(page_number=source.page_number, status="failed",
                warnings=("Layout detection did not return this page.",)))
            continue
        width, height = detected.size.width, detected.size.height
        for cluster in detected.predictions.layout.clusters:
            label = cluster.label.value
            kind = SPECIAL_LABELS.get(label, "text" if label in TEXT_LABELS else "unknown")
            box = cluster.bbox.to_top_left_origin(page_height=height)
            try:
                # Clip small model overshoots at the image boundary.
                bbox = BoundingBox(x0=max(0, min(1, box.l / width)),
                    y0=max(0, min(1, box.t / height)), x1=max(0, min(1, box.r / width)),
                    y1=max(0, min(1, box.b / height)))
                regions.append(LayoutRegion(id=uuid4(), source_label=label, kind=kind,
                    bbox=bbox, confidence=cluster.confidence))
            except (ValueError, ZeroDivisionError):
                warnings.append("A detected region had invalid coordinates or confidence and was omitted.")
        kinds = {region.kind for region in regions}
        for kind in sorted(kinds & {"table", "figure", "equation", "unknown"}):
            warnings.append(f"Detected {kind} content requires specialized extraction or review.")
        if not regions:
            warnings.append("No layout regions detected; inspect the original page.")
        if any(region.confidence < 0.5 for region in regions):
            warnings.append("A layout region has model confidence below 0.5; review its label and boundary.")
        pages.append(LayoutPage(page_number=source.page_number,
            status="needs_review" if warnings else "detected", regions=tuple(regions),
            warnings=tuple(dict.fromkeys(warnings))))
    return tuple(pages)


def detect_pdf_layout(data: bytes, *, document_id: UUID) -> LayoutReport:
    extraction = extract_pdf_text(data, document_id=document_id)
    try:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        raise PDFExtractionError("Install src/backend/requirements-layout.txt to enable layout detection") from None

    options = PdfPipelineOptions(do_ocr=False, do_table_structure=False, enable_remote_services=False)
    options.layout_options.keep_empty_clusters = True
    options.layout_options.model_spec = options.layout_options.model_spec.model_copy(
        update={"repo_id": MODEL_REPOSITORY, "revision": MODEL_REVISION})
    options.accelerator_options.device = "cpu"
    try:
        converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(DocumentStream(name="authorized.pdf", stream=BytesIO(data)),
            raises_on_error=False)
        pages = map_layout_pages(extraction, result.pages)
    except Exception:
        raise PDFExtractionError("Local layout analysis failed; check model availability and the selected PDF") from None
    warnings = []
    if result.status.value != "success":
        warnings.append("Docling reported incomplete conversion; inspect page results.")
    return LayoutReport(native_extraction=extraction, detector="docling-layout-heron",
        model_repository=MODEL_REPOSITORY, model_revision=MODEL_REVISION,
        tool_versions={name: version(name) for name in ("docling", "docling-core", "docling-ibm-models", "torch")},
        options={"ocr": False, "table_structure": False, "keep_empty_clusters": True,
            "remote_services": False, "device": "cpu", "review_confidence_threshold": 0.5},
        pages=pages, warnings=tuple(warnings))


def render_preview(data: bytes, report: LayoutReport) -> str:
    """Self-contained, escaped HTML with rendered pages and detection overlays."""
    import pypdfium2

    colors = {"text": "#39745c", "table": "#a06308", "figure": "#7253a3",
        "equation": "#ad3942", "caption": "#326a91", "unknown": "#555555"}
    sections = []
    with pypdfium2.PdfDocument(data) as pdf:
        for page in report.pages:
            source = pdf[page.page_number - 1]
            try:
                bitmap = source.render(scale=1.25)
                try:
                    image = bitmap.to_pil()
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                finally:
                    bitmap.close()
            finally:
                source.close()
            boxes = []
            for region in page.regions:
                b = region.bbox
                label = escape(f"{region.source_label} ({region.confidence:.2f})")
                boxes.append(f'<div class="box" style="left:{b.x0*100}%;top:{b.y0*100}%;width:{(b.x1-b.x0)*100}%;height:{(b.y1-b.y0)*100}%;border-color:{colors[region.kind]}" title="{label}"><span>{label}</span></div>')
            counts = dict(Counter(region.kind for region in page.regions))
            notes = "".join(f"<li>{escape(warning)}</li>" for warning in page.warnings)
            sections.append(f'<section><h2>Page {page.page_number}</h2><p>{escape(page.status)} | {escape(str(counts))}</p><ul>{notes}</ul><div class="page"><img alt="Original PDF page {page.page_number}" src="data:image/png;base64,{encoded}">{"".join(boxes)}</div></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PDF layout review</title><style>'
        'body{font-family:Arial,sans-serif;background:#f5f5f2;color:#252525;margin:24px}main{max-width:1000px;margin:auto}section{margin:32px 0}.page{position:relative;background:white}.page img{display:block;width:100%}.box{position:absolute;border:2px solid;box-sizing:border-box}.box span{font-size:10px;background:white;color:#222;position:absolute;top:0;left:0;white-space:nowrap}p,li{line-height:1.5}</style><main><h1>PDF layout review</h1>'
        '<p>Model detections are suggestions. Boxes identify regions, not validated extraction accuracy. Native text and citation offsets are retained separately in the JSON.</p>'
        + ''.join(sections) + '</main></html>')


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect PDF layout locally and save a review report.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    if args.preview and args.output.resolve() == args.preview.resolve():
        parser.error("JSON and HTML output paths must differ")
    if args.output.exists() or (args.preview and args.preview.exists()):
        parser.error("Choose new output paths to preserve earlier reports")
    try:
        with args.input.open("rb") as source:
            data = source.read(MAX_PDF_BYTES + 1)
        report = detect_pdf_layout(data, document_id=args.document_id)
        preview = render_preview(data, report) if args.preview else None
        with args.output.open("x", encoding="utf-8") as target:
            target.write(report.model_dump_json(indent=2))
        if args.preview:
            with args.preview.open("x", encoding="utf-8") as target:
                target.write(preview)
    except (OSError, PDFExtractionError) as error:
        parser.exit(1, f"Layout analysis failed: {error}\n")
    for page in report.pages:
        print(f"Page {page.page_number}: {page.status}, {dict(Counter(r.kind for r in page.regions))}")
    print(f"Saved layout report to {args.output}")


if __name__ == "__main__":
    main()
