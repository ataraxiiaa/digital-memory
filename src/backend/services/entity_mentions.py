"""Local GLiNER2 suggestions with validated, exact source evidence."""

import argparse
from importlib.metadata import version
import math
import re
from pathlib import Path
from uuid import uuid5

from ..schemas.entities import EntityMention, EntityReport, PassageEntities
from ..schemas.layout import LayoutReport
from .source_passages import build_source_passages


MODEL_REPOSITORY = "fastino/gliner2-base-v1"
MODEL_REVISION = "f9634218e53580c56edf0de97ca1a7d3f1c2354e"
CORE_LABELS = {
    "person": "A named individual person, including authors and meeting participants",
    "organization": "A named company, institution, university, or organization",
    "project": "A named project or initiative, not a generic task or subject",
}
RESEARCH_LABELS = {
    **CORE_LABELS,
    "model": "A named machine learning model or model architecture, such as Transformer, BERT, or LSTM",
    "method": "A named computational technique or algorithm, such as beam search, dropout, or scaled dot-product attention",
    "dataset": "A named dataset or benchmark collection used for training or evaluation, such as ImageNet or WMT 2014",
    "component": "A technical part of a model architecture, such as encoder, decoder, embedding layer, or attention head",
}
TIMELINE_LABELS = {
    "date": "A specific date, month, year, or time period, such as 3 March 2024 or summer 2019",
    "event": "A named or scheduled event, such as a meeting, conference, workshop, launch, or review",
    "location": "A named place, such as a city, country, office, or venue",
}
FULL_LABELS = {**RESEARCH_LABELS, **TIMELINE_LABELS}
LABEL_SCHEMAS = {"core-v1": CORE_LABELS, "research-v1": RESEARCH_LABELS, "full-v1": FULL_LABELS}
DEFAULT_LABEL_SCHEMA = "full-v1"
LABELS = LABEL_SCHEMAS[DEFAULT_LABEL_SCHEMA]
ALGORITHM_VERSION = "entity-mentions-v2"
CHUNK_SIZE = 128
CHUNK_OVERLAP = 32


_ET_AL = re.compile(r"\s+et\.?\s+al\.?$", re.IGNORECASE)
_CLOCK_TIME = re.compile(r"^\d{1,2}(:\d{2})?\s*([ap]\.?m\.?)$|^\d{1,2}:\d{2}$", re.IGNORECASE)
_DURATION = re.compile(r"^(\d+(\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
                       r"(second|minute|hour|day|week|month|year)s?$", re.IGNORECASE)
_DECIMAL = re.compile(r"^\d+\.\d+$")
_BIBLIOGRAPHY = re.compile(r"arxiv|\bcorr\b|in proceedings|proceedings of|pp\.|journal of|preprint", re.IGNORECASE)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


class _Filtered(Exception):
    """A valid model span removed by a documented cleanup rule."""


def refine_span(label: str, text: str, start: int, end: int) -> tuple[int, int] | None:
    """Apply deterministic, documented cleanups; None drops the span.

    Person names lose a trailing "et al." (the citation convention is not part of a
    name). Dates drop clock times, durations, and bare decimals: scores like 28.4
    and spans like "3.5 days" are not calendar dates. Offsets always stay exact.
    """
    span = text[start:end]
    if label == "person":
        trimmed = _ET_AL.sub("", span)
        if not trimmed.strip():
            return None
        return start, start + len(trimmed)
    if label == "date" and (_CLOCK_TIME.match(span.strip()) or _DURATION.match(span.strip())
                            or _DECIMAL.match(span.strip())):
        return None
    return start, end


def looks_like_bibliography(text: str) -> bool:
    """Reference-list entries mix years with venue words and generate noisy mentions."""
    return len(_YEAR.findall(text)) >= 1 and len(_BIBLIOGRAPHY.findall(text)) >= 1


def load_model():
    """Download only the pinned checkpoint; inference never sends source text."""
    from gliner2 import GLiNER2
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL_REPOSITORY, revision=MODEL_REVISION,
        allow_patterns=["config.json", "encoder_config/config.json", "model.safetensors",
            "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
            "added_tokens.json", "spm.model"])
    return GLiNER2.from_pretrained(path, map_location="cpu").eval()


def extract_entity_mentions(report: LayoutReport, model, *, threshold: float = .5,
                            library_version: str = "2.1.0",
                            labels: dict[str, str] | None = None) -> EntityReport:
    if isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be finite and between zero and one")
    labels = dict(LABELS if labels is None else labels)
    if (not set(CORE_LABELS).issubset(labels) or not set(labels).issubset(FULL_LABELS)
            or any(not isinstance(value, str) or not value.strip() for value in labels.values())):
        raise ValueError("Provide core label descriptions and supported optional labels")
    schema_version = next((name for name, schema in LABEL_SCHEMAS.items() if labels == schema), "custom")
    passages, _ = build_source_passages(report)
    runs = {report.native_extraction.id: report.native_extraction}
    if report.ocr_extraction:
        runs[report.ocr_extraction.id] = report.ocr_extraction
    results = []
    for passage in passages:
        # Verify passage provenance before passing any text to the model.
        if runs[passage.citation.extraction_id].resolve_citation(passage.citation) != passage.text:
            raise ValueError("Passage citation does not resolve to its text")
        try:
            raw = model.extract_entities_long(passage.text, labels, threshold=threshold,
                chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, batch_size=1,
                include_confidence=True, include_spans=True)
            entities = raw["entities"]
            if not isinstance(entities, dict):
                raise ValueError("Invalid model response")
            bibliography = looks_like_bibliography(passage.text)
            mentions = []
            rejected = 0
            filtered = 0
            seen = set()
            for label, candidates in entities.items():
                if label not in labels or not isinstance(candidates, list):
                    raise ValueError("Invalid model label or candidate list")
                for candidate in candidates:
                    try:
                        start, end = candidate["start"], candidate["end"]
                        text, score = candidate["text"], candidate["confidence"]
                        if (type(start) is not int or type(end) is not int
                                or not 0 <= start < end <= len(passage.text)
                                or not isinstance(text, str) or not text.strip()
                                or passage.text[start:end] != text
                                or type(score) not in (int, float) or not math.isfinite(score)
                                or not threshold <= score <= 1):
                            raise ValueError("Invalid span or score")
                        refined = refine_span(label, passage.text, start, end)
                        if refined is None:
                            raise _Filtered
                        start, end = refined
                        text = passage.text[start:end]
                        citation = passage.citation.model_copy(update={
                            "start_char": passage.citation.start_char + start,
                            "end_char": passage.citation.start_char + end})
                        if runs[citation.extraction_id].resolve_citation(citation) != text:
                            raise ValueError("Invalid citation")
                    except _Filtered:
                        filtered += 1
                        continue
                    except (KeyError, TypeError, ValueError):
                        rejected += 1
                        continue
                    key = (label, start, end)
                    if key in seen:
                        continue
                    seen.add(key)
                    identity = f"{ALGORITHM_VERSION}|{MODEL_REVISION}|{library_version}|{threshold}|{labels}|{CHUNK_SIZE}|{CHUNK_OVERLAP}|{label}|{start}|{end}"
                    mentions.append(EntityMention(id=uuid5(passage.id, identity),
                        passage_id=passage.id, label=label, text=text,
                        start_char=start, end_char=end, citation=citation,
                        source_sha256=passage.source_sha256, score=score,
                        warnings=tuple(dict.fromkeys([*passage.warnings,
                            *(["Looks like a bibliography entry"] if bibliography else []),
                            *(["Source passage requires review"] if passage.review_required else [])]))))
            labels_by_span = {}
            for mention in mentions:
                labels_by_span.setdefault((mention.start_char, mention.end_char), set()).add(mention.label)
            ambiguous_spans = {span for span, types in labels_by_span.items() if len(types) > 1}
            for index, mention in enumerate(mentions):
                if (mention.start_char, mention.end_char) in ambiguous_spans:
                    notes = (*mention.warnings, "Multiple entity types suggested for this exact span")
                    mentions[index] = mention.model_copy(update={"warnings": tuple(dict.fromkeys(notes))})
            warnings = list(passage.warnings)
            if ambiguous_spans:
                warnings.append("Conflicting entity types require review")
            if passage.review_required:
                warnings.append("Source passage requires review")
            if rejected:
                warnings.append("Invalid model candidates were rejected")
            if bibliography:
                warnings.append("Looks like a bibliography entry; suggestions here are often noise")
            results.append(PassageEntities(passage_id=passage.id,
                status="needs_review" if warnings else "completed",
                mentions=tuple(sorted(mentions, key=lambda item: (item.start_char, item.end_char, item.label))),
                rejected_candidate_count=rejected, filtered_candidate_count=filtered, warnings=tuple(dict.fromkeys(warnings))))
        except Exception:
            # Keep failures distinct from successful extraction with zero mentions.
            # Do not put provider exceptions (which can contain source text) in reports.
            results.append(PassageEntities(passage_id=passage.id, status="failed",
                warnings=("Local entity extraction failed for this passage",)))
    return EntityReport(document_id=report.native_extraction.document_id,
        native_extraction_id=report.native_extraction.id,
        ocr_extraction_id=report.ocr_extraction.id if report.ocr_extraction else None,
        source_sha256=report.native_extraction.source_sha256,
        label_schema_version=schema_version,
        model_repository=MODEL_REPOSITORY, model_revision=MODEL_REVISION,
        library_version=library_version, threshold=threshold, labels=labels,
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, passages=tuple(results))


def main():
    parser = argparse.ArgumentParser(description="Extract local, evidence-backed entity suggestions.")
    parser.add_argument("input", type=Path, help="Saved LayoutReport JSON")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold", default=.5, type=float)
    parser.add_argument("--schema", choices=LABEL_SCHEMAS, default=DEFAULT_LABEL_SCHEMA)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(1, "Output already exists; choose a new filename.\n")
    try:
        report = LayoutReport.model_validate_json(args.input.read_text(encoding="utf-8"))
        result = extract_entity_mentions(report, load_model(), threshold=args.threshold,
            library_version=version("gliner2"), labels=LABEL_SCHEMAS[args.schema])
        with args.output.open("x", encoding="utf-8") as target:
            target.write(result.model_dump_json(indent=2))
    except (OSError, ValueError, ImportError, RuntimeError):
        parser.exit(1, "Entity extraction could not complete; check dependencies, input, model cache, and output path.\n")
    count = sum(len(page.mentions) for page in result.passages)
    failed = sum(page.status == "failed" for page in result.passages)
    print(f"Saved {count} suggested mentions across {len(result.passages)} passages; {failed} failed.")
    if failed:
        parser.exit(1)


if __name__ == "__main__":
    main()
