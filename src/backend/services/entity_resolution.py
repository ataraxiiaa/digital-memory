"""Deterministic name matching and merge suggestions for canonical entities.

Pure functions, no I/O. Nothing here decides that two names are the same
person: it proposes links and merges with a score and a human-readable
reason, and a person confirms them.
"""

from dataclasses import dataclass, field
import re
import unicodedata
from uuid import UUID, uuid4

TITLES = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "professor", "sir", "dame"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd"}
PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "bin", "ibn", "al", "el", "le", "la"}
ORG_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "plc",
                "gmbh", "ag", "sa", "pvt", "pty", "lp", "llp"}
# Letters Unicode decomposition leaves alone.
TRANSLITERATE = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae",
                               "Æ": "AE", "œ": "oe", "Œ": "OE", "ı": "i"})
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+", re.UNICODE)
_ET_AL = re.compile(r"\s+et\.?\s+al\.?\s*$", re.IGNORECASE)

PERSON_KINDS = ("exact", "initial", "surname")


def fold(text: str) -> str:
    """Case-fold, strip diacritics, and collapse whitespace."""
    text = unicodedata.normalize("NFKD", text.translate(TRANSLITERATE))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


_PERSON_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


def _words(text: str, *, person: bool = False) -> list[str]:
    if person:
        # Keep "Mary-Jane" as one given name so it is not read as a middle name.
        return _PERSON_WORD.findall(fold(text).replace("–", "-"))
    # Hyphenated words are split so "Self-Attention" equals "self attention".
    return _WORD.findall(fold(text).replace("-", " ").replace("–", " "))


@dataclass(frozen=True)
class PersonName:
    given: tuple[str, ...]
    last: str

    @property
    def key(self) -> str:
        return " ".join((*self.given, self.last))


def parse_person(text: str) -> PersonName | None:
    text = _ET_AL.sub("", text.strip())
    if "," in text:
        family, _, given_part = text.partition(",")
        family_words, given_words = _words(family, person=True), _words(given_part, person=True)
    else:
        words = _words(text, person=True)
        while len(words) > 1 and words[-1] in NAME_SUFFIXES:
            words.pop()
        end = len(words) - 1
        # Walk back over surname particles (van der Berg, bin Rashid).
        start = end
        while start > 0 and words[start - 1] in PARTICLES:
            start -= 1
        family_words, given_words = words[start:end + 1], words[:start]
    given_words = [w for w in given_words if w not in TITLES and w not in NAME_SUFFIXES]
    family_words = [w for w in family_words if w not in NAME_SUFFIXES]
    if not family_words or not any(ch.isalpha() for ch in " ".join(family_words)):
        return None
    return PersonName(tuple(given_words), " ".join(family_words))


@dataclass(frozen=True)
class Match:
    kind: str  # exact | initial | surname | related
    score: float
    reason: str


def match_persons(a_text: str, b_text: str) -> Match | None:
    """Compare two person names; None means no match or a definite conflict."""
    a, b = parse_person(a_text), parse_person(b_text)
    if a is None or b is None or a.last != b.last:
        return None
    if a.key == b.key:
        return Match("exact", 1.0, "Same name after ignoring case, accents, titles and word order")
    if not a.given or not b.given:
        return Match("surname", 0.4,
            f"“{a_text.strip()}” and “{b_text.strip()}” share a surname but one has no first name")
    used_initial = False
    for x, y in zip(a.given, b.given):
        if len(x) == 1 or len(y) == 1:
            if x[0] != y[0]:
                return None
            used_initial = used_initial or x != y
        elif x != y:
            return None  # two different full first names: a definite conflict
    if used_initial:
        return Match("initial", 0.85, f"“{a_text.strip()}” may be “{b_text.strip()}” (first name abbreviated to an initial)")
    return Match("initial", 0.8, f"“{a_text.strip()}” and “{b_text.strip()}” differ only by a middle name or initial")


def org_key(text: str) -> tuple[str, ...]:
    words = _words(text)
    while len(words) > 1 and words[-1] in ORG_SUFFIXES:
        words.pop()
    return tuple(words)


def match_organizations(a_text: str, b_text: str) -> Match | None:
    a, b = org_key(a_text), org_key(b_text)
    if not a or not b:
        return None
    if a == b:
        return Match("exact", 1.0, "Same name after ignoring case and legal suffixes such as Inc. or Ltd")
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if long_[:len(short)] == short:
        return Match("related", 0.45, "One name extends the other, so they may be the same organization or a "
            "part of it (for example a university and its department)")
    return None


def generic_key(text: str) -> str:
    return " ".join(_words(text))


def entity_key(entity_type: str, text: str) -> str:
    """Grouping key: two same-type mentions with equal keys name the same entity."""
    if entity_type == "person":
        person = parse_person(text)
        return person.key if person else generic_key(text)
    if entity_type == "organization":
        return " ".join(org_key(text))
    return generic_key(text)


def match_names(entity_type: str, a: str, b: str) -> Match | None:
    if entity_type == "person":
        return match_persons(a, b)
    if entity_type == "organization":
        return match_organizations(a, b)
    return Match("exact", 1.0, "Same name after ignoring case and punctuation") if (
        generic_key(a) and generic_key(a) == generic_key(b)) else None


@dataclass(frozen=True)
class MentionRef:
    run_id: UUID
    mention_id: UUID
    document_id: UUID
    type: str
    text: str


@dataclass(frozen=True)
class EntityState:
    id: UUID
    type: str
    canonical_name: str
    aliases: tuple[str, ...]  # raw text of currently linked mentions


@dataclass(frozen=True)
class NewEntity:
    id: UUID
    type: str
    canonical_name: str


@dataclass(frozen=True)
class Link:
    entity_id: UUID
    run_id: UUID
    mention_id: UUID
    method: str = "exact"


@dataclass(frozen=True)
class Suggestion:
    entity_a: UUID
    entity_b: UUID
    kind: str
    score: float
    ambiguous: bool
    reason: str


@dataclass
class Plan:
    new_entities: list[NewEntity] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    skipped_ambiguous: int = 0


def canonical_name(texts: list[str]) -> str:
    """Most frequent spelling. Ties prefer normal capitalization, no title, then the longer text."""
    counts: dict[str, int] = {}
    for text in texts:
        cleaned = re.sub(r"\s+", " ", text).strip()
        counts[cleaned] = counts.get(cleaned, 0) + 1

    def rank(text: str):
        first = fold(text).split(" ")[0].rstrip(".") if text else ""
        return (-counts[text], text.islower() or text.isupper(), first in TITLES, -len(text), text)
    return sorted(counts, key=rank)[0]


def best_match(entity_type: str, a: EntityState, b: EntityState) -> Match | None:
    best = None
    for x in dict.fromkeys(a.aliases or (a.canonical_name,)):
        for y in dict.fromkeys(b.aliases or (b.canonical_name,)):
            found = match_names(entity_type, x, y)
            if found and (best is None or found.score > best.score):
                best = found
    return best


def resolve(unlinked: list[MentionRef], entities: list[EntityState],
            new_id=uuid4) -> Plan:
    """Plan links for unlinked accepted mentions and merge suggestions between entities.

    A mention joins an existing entity only when its normalized text equals one of
    that entity's aliases; if several entities qualify the mention is left alone
    (counted in skipped_ambiguous). Otherwise identical-normalized mentions of the
    same type are grouped into one new entity. Everything fuzzy becomes a
    suggestion for a person to confirm.
    """
    plan = Plan()
    alias_index: dict[tuple[str, str], list[UUID]] = {}
    for entity in entities:
        for alias in entity.aliases:
            key = (entity.type, entity_key(entity.type, alias))
            if entity.id not in alias_index.setdefault(key, []):
                alias_index[key].append(entity.id)
    groups: dict[tuple[str, str], list[MentionRef]] = {}
    for mention in unlinked:
        key = (mention.type, entity_key(mention.type, mention.text))
        if not key[1]:
            continue
        owners = alias_index.get(key, [])
        if len(owners) == 1:
            plan.links.append(Link(owners[0], mention.run_id, mention.mention_id))
        elif len(owners) > 1:
            plan.skipped_ambiguous += 1
        else:
            groups.setdefault(key, []).append(mention)
    states = list(entities)
    for (entity_type, _), members in sorted(groups.items(), key=lambda item: item[0]):
        entity = NewEntity(new_id(), entity_type, canonical_name([m.text for m in members]))
        plan.new_entities.append(entity)
        plan.links.extend(Link(entity.id, m.run_id, m.mention_id) for m in members)
        states.append(EntityState(entity.id, entity_type, entity.canonical_name, tuple(m.text for m in members)))
    # Aliases gained by existing entities in this plan also take part in matching.
    gained: dict[UUID, list[str]] = {}
    by_mention = {(m.run_id, m.mention_id): m for m in unlinked}
    for link in plan.links:
        gained.setdefault(link.entity_id, []).append(by_mention[(link.run_id, link.mention_id)].text)
    states = [EntityState(s.id, s.type, s.canonical_name, (*s.aliases, *gained.get(s.id, ()))) if s.id in gained
              and s.id in {e.id for e in entities} else s for s in states]
    plan.suggestions = suggest_merges(states)
    return plan


def suggest_merges(states: list[EntityState]) -> list[Suggestion]:
    by_type: dict[str, list[EntityState]] = {}
    for state in states:
        by_type.setdefault(state.type, []).append(state)
    suggestions: list[Suggestion] = []
    for entity_type, group in by_type.items():
        if entity_type not in ("person", "organization"):
            continue  # other types only group exact matches, never fuzzy merges
        pairs: dict[tuple[UUID, UUID], tuple[EntityState, EntityState, Match]] = {}
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                found = best_match(entity_type, a, b)
                if found:
                    pairs[(a.id, b.id)] = (a, b, found)
        # An entity that fits several mutually incompatible entities is ambiguous.
        neighbours: dict[UUID, list[EntityState]] = {}
        for (a, b, found) in pairs.values():
            if found.kind in ("initial", "surname"):
                neighbours.setdefault(a.id, []).append(b)
                neighbours.setdefault(b.id, []).append(a)
        ambiguous_with: dict[UUID, list[EntityState]] = {}
        for entity_id, others in neighbours.items():
            for i, x in enumerate(others):
                for y in others[i + 1:]:
                    if best_match(entity_type, x, y) is None:
                        for item in (x, y):
                            ambiguous_with.setdefault(entity_id, [])
                            if item not in ambiguous_with[entity_id]:
                                ambiguous_with[entity_id].append(item)
        for (a, b, found) in pairs.values():
            ambiguous = False
            reason = found.reason
            for center, other in ((a, b), (b, a)):
                rivals = [r for r in ambiguous_with.get(center.id, [])
                          if r.id != other.id and best_match(entity_type, other, r) is None]
                if other in ambiguous_with.get(center.id, []) and rivals:
                    ambiguous = True
                    names = ", ".join(f"“{r.canonical_name}”" for r in rivals)
                    reason += f". Ambiguous: “{center.canonical_name}” also fits {names}, which look like different people"
            low, high = sorted((a.id, b.id), key=str)
            suggestions.append(Suggestion(low, high, found.kind, found.score, ambiguous, reason))
    return sorted(suggestions, key=lambda s: (-s.score, str(s.entity_a), str(s.entity_b)))
