"""Propose relationships between ACCEPTED mentions in the same sentence.

The local GLiNER2 relation extractor is run on one sentence at a time, constrained to
a small closed set of relation types. Its head and tail spans must exactly equal two
accepted mentions, and the mention types must fit the relation. Deterministic guards
then drop negated, hedged, and ended statements using only the text between the two
mentions. Nothing is auto-confirmed and every candidate keeps its exact evidence.
"""

from dataclasses import dataclass
import re
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from ..schemas.extraction import CitationReference
from ..schemas.layout import LayoutReport
from ..schemas.relationships import RelationBuild, RelationCounts, RelationDraft, RelationEnd
from .sentences import sentence_spans

# type -> (allowed subject labels, allowed object labels, description sent to the model, symmetric)
RELATION_TYPES: dict[str, tuple[frozenset[str], frozenset[str], str, bool]] = {
    "works_at": (frozenset({"person"}), frozenset({"organization"}),
        "A person works for, is employed by, or holds a role at an organization", False),
    "member_of": (frozenset({"person"}), frozenset({"organization", "project"}),
        "A person is a member of, belongs to, or leads an organization or project", False),
    "located_in": (frozenset({"organization", "event", "project"}), frozenset({"location"}),
        "An organization, event, or project is based in or located in a place", False),
    "attended": (frozenset({"person"}), frozenset({"event"}),
        "A person attended or spoke at an event", False),
    "part_of": (frozenset({"component", "model", "organization"}), frozenset({"model", "project", "organization"}),
        "Something is a component of, part of, or a subsidiary of a larger model, project, or organization", False),
    "collaborated_with": (frozenset({"person"}), frozenset({"person"}),
        "Two people worked or collaborated together", True),
}
# part_of only pairs like with like, or a component with a model or project.
PART_OF_PAIRS = {("component", "model"), ("component", "project"), ("model", "model"), ("model", "project"),
    ("organization", "organization")}

_NEGATION = re.compile(r"\b(not|never|no longer|cannot|neither|nor|without|denied|refused)\b|n't", re.IGNORECASE)
_HEDGE = re.compile(r"\b(may|might|could|possibly|perhaps|reportedly|allegedly|rumou?red|apparently|likely|supposedly|probably|if)\b", re.IGNORECASE)
_ENDED = re.compile(r"\b(left|quit|resigned|formerly|previously|used to|former|ex-)\b", re.IGNORECASE)
# The model over-reads two people in one sentence as collaborators, so collaboration
# needs an explicit cue. Origins, visits and meetings are not a location relationship.
_COLLAB_CUE = re.compile(r"collaborat|co-?author|co-?found|co-?wrote|work(?:s|ed|ing)? (?:together|with|alongside)"
    r"|partner|joint|team(?:s|ed)? up|colleague", re.IGNORECASE)
_NOT_LOCATED = re.compile(r"\b(founded|born|raised|visited|travell?ed|met|moved|flew)\b", re.IGNORECASE)
NAMESPACE = uuid5(NAMESPACE_URL, "digital-memory/relationship")


@dataclass(frozen=True)
class AcceptedMention:
    """An accepted mention with its effective (possibly corrected) type."""

    run_id: UUID
    mention_id: UUID
    label: str
    text: str
    citation: CitationReference


def _region_text(report: LayoutReport, citation: CitationReference) -> str | None:
    for run in (report.native_extraction, report.ocr_extraction):
        if run is None or run.id != citation.extraction_id:
            continue
        for page in run.pages:
            for region in page.regions:
                if region.id == citation.region_id:
                    return region.text
    return None


def _end(mention: AcceptedMention) -> RelationEnd:
    return RelationEnd(run_id=mention.run_id, mention_id=mention.mention_id, label=mention.label,
        text=mention.text, citation=mention.citation)


def _key(mention: AcceptedMention) -> str:
    c = mention.citation
    return f"{c.region_id}:{c.start_char}:{c.end_char}"


def guard_reason(between: str) -> str | None:
    """Why the text between two mentions makes the statement unusable, if it does."""
    if _NEGATION.search(between):
        return "negated"
    if _HEDGE.search(between):
        return "hedged"
    if _ENDED.search(between):
        return "ended"
    return None


def valid_labels(relation: str, subject_label: str, object_label: str) -> bool:
    """Whether a relation type fits the two mention types in this direction."""
    subjects, objects, _, _ = RELATION_TYPES[relation]
    if subject_label not in subjects or object_label not in objects:
        return False
    return relation != "part_of" or (subject_label, object_label) in PART_OF_PAIRS


def valid_pair(relation: str, subject: AcceptedMention, obj: AcceptedMention) -> bool:
    return valid_labels(relation, subject.label, obj.label) and subject.mention_id != obj.mention_id


def parse_relations(raw: dict) -> list[tuple[str, dict, dict]]:
    """Flatten the model output to (type, head, tail). Unknown types and malformed items are skipped."""
    found = []
    container = raw.get("relation_extraction") if isinstance(raw, dict) else None
    if not isinstance(container, dict):
        return found
    for key, items in container.items():
        name = str(key).split(":", 1)[0].strip()
        if name not in RELATION_TYPES or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("head"), dict) and isinstance(item.get("tail"), dict):
                found.append((name, item["head"], item["tail"]))
    return found


def build_relation_candidates(report: LayoutReport, mentions: Sequence[AcceptedMention], model, *,
                              threshold: float = 0.5, method: str = "gliner2-relations") -> RelationBuild:
    document_id = report.native_extraction.document_id
    counts = {"sentences_checked": 0, "filtered_negated": 0, "filtered_hedged": 0, "filtered_ended": 0, "filtered_cue": 0,
        "invalid_types": 0, "unmatched_spans": 0, "failed_sentences": 0}
    by_region: dict[UUID, list[AcceptedMention]] = {}
    for mention in mentions:
        by_region.setdefault(mention.citation.region_id, []).append(mention)
    descriptions = {name: spec[2] for name, spec in RELATION_TYPES.items()}
    drafts: dict[UUID, RelationDraft] = {}
    for region_mentions in by_region.values():
        text = _region_text(report, region_mentions[0].citation)
        if text is None:
            continue
        for start, end in sentence_spans(text):
            inside = [m for m in region_mentions if start <= m.citation.start_char and m.citation.end_char <= end]
            if len(inside) < 2:
                continue
            counts["sentences_checked"] += 1
            sentence = text[start:end]
            by_span = {(m.citation.start_char - start, m.citation.end_char - start): m for m in inside}
            try:
                raw = model.extract_relations(sentence, descriptions, threshold=threshold,
                    include_confidence=True, include_spans=True)
                parsed = parse_relations(raw)
            except Exception:
                # Never put provider errors (which can quote source text) into results.
                counts["failed_sentences"] += 1
                continue
            for relation, head, tail in parsed:
                try:
                    subject = by_span.get((head["start"], head["end"]))
                    obj = by_span.get((tail["start"], tail["end"]))
                    score = min(float(head["confidence"]), float(tail["confidence"]))
                except (KeyError, TypeError, ValueError):
                    counts["unmatched_spans"] += 1
                    continue
                if subject is None or obj is None or not 0 <= score <= 1 or score != score:
                    counts["unmatched_spans"] += 1
                    continue
                if RELATION_TYPES[relation][3] and subject.citation.start_char > obj.citation.start_char:
                    subject, obj = obj, subject
                if not valid_pair(relation, subject, obj):
                    counts["invalid_types"] += 1
                    continue
                first, second = sorted((subject, obj), key=lambda m: m.citation.start_char)
                between = text[first.citation.end_char:second.citation.start_char]
                reason = guard_reason(between)
                if reason:
                    counts[f"filtered_{reason}"] += 1
                    continue
                if (relation == "collaborated_with" and not _COLLAB_CUE.search(sentence)) or (
                        relation == "located_in" and _NOT_LOCATED.search(between)):
                    counts["filtered_cue"] += 1
                    continue
                identity = f"{document_id}|{relation}|{_key(subject)}|{_key(obj)}"
                draft = RelationDraft(id=uuid5(NAMESPACE, identity), document_id=document_id, type=relation,
                    subject=_end(subject), object=_end(obj), page_number=subject.citation.page_number,
                    sentence_text=sentence, sentence_start=start, sentence_end=end, method=method, score=score)
                if draft.id not in drafts or drafts[draft.id].score < draft.score:
                    drafts[draft.id] = draft
    ordered = sorted(drafts.values(), key=lambda d: (d.page_number, d.sentence_start,
        d.subject.citation.start_char, d.type, str(d.id)))
    return RelationBuild(candidates=tuple(ordered), counts=RelationCounts(**counts))
