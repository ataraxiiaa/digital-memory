"""Canonical entities, merge suggestions, and merges through ownership-checked RPCs and RLS reads."""

from collections import defaultdict
from uuid import UUID, uuid4

from fastapi import HTTPException

from ..schemas.entity_graph import (
    EntityDetail, EntityList, EntityRecord, MentionEvidence, MergeRecord, MergeSuggestion,
    ResolveResult, SuggestionList, SuggestionSide,
)
from ..services.entity_resolution import EntityState, MentionRef, Plan, resolve
from .documents import StorageUnavailable
from .entities import SupabaseEntityStore

PAGE_CAP = 20000
BATCH = 40
EVIDENCE_PER_ENTITY = 3
LINK_SELECT = "entity_id,run_id,mention_id,document_id,entity_mentions(text,page_number,citation)"


def plan_payload(plan: Plan) -> dict:
    return {
        "p_entities": [{"id": str(e.id), "type": e.type, "canonical_name": e.canonical_name} for e in plan.new_entities],
        "p_links": [{"entity_id": str(l.entity_id), "run_id": str(l.run_id), "mention_id": str(l.mention_id)}
                    for l in plan.links],
        "p_suggestions": [{"entity_a": str(s.entity_a), "entity_b": str(s.entity_b), "kind": s.kind,
                           "score": s.score, "ambiguous": s.ambiguous, "reason": s.reason}
                          for s in plan.suggestions],
    }


def in_list(ids) -> str:
    return "in.(" + ",".join(str(i) for i in ids) + ")"


def chunks(values: list, size: int = BATCH):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def evidence_from(row: dict) -> MentionEvidence:
    mention = row["entity_mentions"]
    return MentionEvidence(run_id=row["run_id"], mention_id=row["mention_id"], document_id=row["document_id"],
        page_number=mention["page_number"], text=mention["text"], citation=mention["citation"])


class SupabaseEntityGraphStore(SupabaseEntityStore):
    """Shares request handling with the mention store; adds identity operations."""

    async def _rows(self, path: str, **params) -> list:
        rows = await self._json("GET", path, params=params)
        if not isinstance(rows, list):
            raise StorageUnavailable("Invalid entity storage response")
        return rows

    async def _links(self, entity_ids: list[UUID]) -> list[dict]:
        rows: list[dict] = []
        for batch in chunks(entity_ids):
            rows += await self._rows("entity_links", select=LINK_SELECT, entity_id=in_list(batch),
                order="linked_at.asc", limit=PAGE_CAP)
        return rows

    async def resolve(self) -> ResolveResult:
        refreshed = await self._json("POST", "rpc/refresh_entity_links", json={})
        runs = await self._rows("entity_runs", select="id,document_id,started_at,status",
            status="in.(completed,needs_review)", order="started_at.desc", limit=PAGE_CAP)
        latest: dict[str, str] = {}
        for run in runs:
            latest.setdefault(run["document_id"], run["id"])
        accepted: list[dict] = []
        for batch in chunks(list(latest.values())):
            accepted += await self._rows("entity_mentions",
                select="id,run_id,document_id,text,label,reviewed_label",
                run_id=in_list(batch), review_status="eq.accepted", limit=PAGE_CAP)
        entities = await self._rows("entity_links", select="entity_id,run_id,mention_id,entity_mentions(text)", limit=PAGE_CAP)
        linked = {(row["run_id"], row["mention_id"]) for row in entities}
        aliases: dict[str, list[str]] = defaultdict(list)
        for row in entities:
            aliases[row["entity_id"]].append(row["entity_mentions"]["text"])
        existing = await self._rows("entities", select="id,type,canonical_name", status="neq.merged", limit=PAGE_CAP)
        states = [EntityState(UUID(e["id"]), e["type"], e["canonical_name"], tuple(aliases.get(e["id"], ())))
                  for e in existing]
        unlinked = [MentionRef(UUID(m["run_id"]), UUID(m["id"]), UUID(m["document_id"]),
                               m["reviewed_label"] or m["label"], m["text"])
                    for m in accepted if (m["run_id"], m["id"]) not in linked]
        plan = resolve(unlinked, states, new_id=uuid4)
        applied = await self._json("POST", "rpc/apply_entity_resolution", json=plan_payload(plan))
        try:
            return ResolveResult(**refreshed, **applied, skipped_ambiguous=plan.skipped_ambiguous)
        except (TypeError, ValueError):
            raise StorageUnavailable("Invalid entity storage response") from None

    async def _entity_rows(self, ids: list[UUID]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for batch in chunks(ids):
            for row in await self._rows("entities", select="*", id=in_list(batch), limit=PAGE_CAP):
                found[row["id"]] = row
        return found

    @staticmethod
    def _record(row: dict, links: list[dict]) -> EntityRecord:
        texts = [l["entity_mentions"]["text"] for l in links]
        spellings = list(dict.fromkeys(" ".join(t.split()) for t in texts))
        return EntityRecord(**{**row, "mention_count": len(links),
            "document_count": len({l["document_id"] for l in links}), "aliases": spellings})

    async def list_entities(self, *, type_: str | None, status: str | None, q: str | None,
                            limit: int, offset: int) -> EntityList:
        params = {"select": "*", "status": f"eq.{status}" if status else "neq.merged",
            "order": "canonical_name.asc,id.asc", "limit": limit, "offset": offset}
        if type_:
            params["type"] = f"eq.{type_}"
        if q:
            cleaned = "".join(ch for ch in q if ch not in "*,()%\\:.\"'").strip()
            if cleaned:
                params["canonical_name"] = f"ilike.*{cleaned}*"
        response = await self._send("GET", "entities", params=params, headers={"Prefer": "count=exact"})
        try:
            rows = response.json()
            total = int(response.headers.get("content-range", "").rpartition("/")[2])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid entity storage response") from None
        by_entity: dict[str, list[dict]] = defaultdict(list)
        for link in await self._links([UUID(r["id"]) for r in rows]):
            by_entity[link["entity_id"]].append(link)
        try:
            records = [self._record(r, by_entity[r["id"]]) for r in rows]
        except (ValueError, TypeError, KeyError):
            raise StorageUnavailable("Invalid entity storage response") from None
        return EntityList(total=total, offset=offset, limit=limit, entities=records)

    async def get_entity(self, entity_id: UUID) -> EntityDetail:
        rows = await self._rows("entities", select="*", id=f"eq.{entity_id}", limit=1)
        if not rows:
            raise HTTPException(404, "Not found")
        links = await self._links([entity_id])
        merges = await self._rows("entity_merges", select="id,absorbed_id,absorbed_name,merged_at,undone_at",
            keep_id=f"eq.{entity_id}", order="merged_at.desc", limit=200)
        try:
            return EntityDetail(entity=self._record(rows[0], links),
                mentions=[evidence_from(l) for l in links],
                merges=[MergeRecord.model_validate(m) for m in merges])
        except (ValueError, TypeError, KeyError):
            raise StorageUnavailable("Invalid entity storage response") from None

    async def list_suggestions(self, *, status: str, min_score: float | None, limit: int,
                               offset: int) -> SuggestionList:
        params = {"select": "*", "status": f"eq.{status}", "order": "score.desc,id.asc", "limit": limit, "offset": offset}
        if min_score is not None:
            params["score"] = f"gte.{min_score}"
        response = await self._send("GET", "merge_suggestions", params=params, headers={"Prefer": "count=exact"})
        try:
            rows = response.json()
            total = int(response.headers.get("content-range", "").rpartition("/")[2])
        except (ValueError, TypeError):
            raise StorageUnavailable("Invalid entity storage response") from None
        ids = list({UUID(r[side]) for r in rows for side in ("entity_a", "entity_b")})
        entity_rows = await self._entity_rows(ids)
        by_entity: dict[str, list[dict]] = defaultdict(list)
        for link in await self._links(ids):
            by_entity[link["entity_id"]].append(link)

        def side(entity_id: str) -> SuggestionSide:
            record = self._record(entity_rows[entity_id], by_entity[entity_id])
            return SuggestionSide(id=record.id, type=record.type, canonical_name=record.canonical_name,
                status=record.status, mention_count=record.mention_count, aliases=record.aliases,
                evidence=[evidence_from(l) for l in by_entity[entity_id][:EVIDENCE_PER_ENTITY]])
        try:
            suggestions = [MergeSuggestion(id=r["id"], kind=r["kind"], score=r["score"], ambiguous=r["ambiguous"],
                reason=r["reason"], status=r["status"], a=side(r["entity_a"]), b=side(r["entity_b"]))
                for r in rows if r["entity_a"] in entity_rows and r["entity_b"] in entity_rows]
        except (ValueError, TypeError, KeyError):
            raise StorageUnavailable("Invalid entity storage response") from None
        return SuggestionList(total=total, offset=offset, limit=limit, suggestions=suggestions)

    async def merge(self, keep_id: UUID, absorb_id: UUID) -> UUID:
        result = await self._json("POST", "rpc/merge_entities", json={"p_keep": str(keep_id), "p_absorb": str(absorb_id)})
        try:
            return UUID(result)
        except (ValueError, TypeError, AttributeError):
            raise StorageUnavailable("Could not confirm the merge") from None

    async def confirm_suggestion(self, suggestion_id: UUID, keep_id: UUID | None) -> UUID:
        rows = await self._rows("merge_suggestions", select="entity_a,entity_b,status", id=f"eq.{suggestion_id}", limit=1)
        if not rows or rows[0]["status"] != "pending":
            raise HTTPException(404, "Not found")
        a, b = UUID(rows[0]["entity_a"]), UUID(rows[0]["entity_b"])
        if keep_id is not None and keep_id not in (a, b):
            raise HTTPException(422, "Choose one of the two suggested entities.")
        if keep_id is None:
            counts = {a: 0, b: 0}
            for link in await self._links([a, b]):
                counts[UUID(link["entity_id"])] += 1
            keep_id = a if counts[a] >= counts[b] else b
        await self.merge(keep_id, b if keep_id == a else a)
        return keep_id

    async def undo_merge(self, merge_id: UUID) -> None:
        await self._send("POST", "rpc/undo_merge", json={"p_merge_id": str(merge_id)})

    async def rename(self, entity_id: UUID, name: str) -> None:
        await self._send("POST", "rpc/rename_entity", json={"p_id": str(entity_id), "p_name": name})

    async def confirm_entity(self, entity_id: UUID) -> None:
        await self._send("POST", "rpc/confirm_entity", json={"p_id": str(entity_id)})

    async def dismiss(self, suggestion_id: UUID) -> None:
        await self._send("POST", "rpc/dismiss_merge_suggestion", json={"p_id": str(suggestion_id)})

    async def link(self, entity_id: UUID, run_id: UUID, mention_id: UUID) -> None:
        await self._send("POST", "rpc/link_mention", json={
            "p_run_id": str(run_id), "p_mention_id": str(mention_id), "p_entity_id": str(entity_id)})

    async def split(self, run_id: UUID, mention_id: UUID) -> UUID:
        new_id = uuid4()
        await self._send("POST", "rpc/split_mention", json={
            "p_run_id": str(run_id), "p_mention_id": str(mention_id), "p_new_id": str(new_id)})
        return new_id
