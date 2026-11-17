"""Entity-level graph from CONFIRMED relationships.

Nodes are canonical entities (Phase 2) reached through entity_links, so a merge
collapses nodes without touching relationships. A mention with no entity link is its
own node. Suggested, rejected, and stale relationships never appear.
"""

from typing import Iterable, Mapping

from ..schemas.relationships import Graph, GraphEdge, GraphNode, Relationship
from .relations import RELATION_TYPES


def build_graph(relationships: Iterable[Relationship], links: Mapping[tuple[str, str], str],
                entities: Mapping[str, Mapping]) -> Graph:
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], list] = {}

    def node_for(end) -> str:
        entity_id = links.get((str(end.run_id), str(end.mention_id)))
        entity = entities.get(entity_id) if entity_id else None
        if entity and entity.get("status") != "merged":
            key = str(entity["id"])
            current = nodes.get(key)
            nodes[key] = GraphNode(id=key, name=entity["canonical_name"], type=entity["type"],
                entity_id=entity["id"], mention_count=(current.mention_count if current else 0) + 1)
            return key
        key = f"m:{end.run_id}:{end.mention_id}"
        nodes[key] = GraphNode(id=key, name=end.text, type=end.label, mention_count=1)
        return key

    used = 0
    for item in relationships:
        if item.review_status != "confirmed" or item.stale:
            continue
        kind = item.corrected_type or item.type
        first, second = (item.object, item.subject) if item.swapped else (item.subject, item.object)
        source, target = node_for(first), node_for(second)
        if source == target:
            continue
        if RELATION_TYPES[kind][3] and source > target:
            source, target = target, source
        edges.setdefault((kind, source, target), []).append(item.id)
        used += 1
    return Graph(
        nodes=sorted(nodes.values(), key=lambda n: (n.type, n.name.lower(), n.id)),
        edges=[GraphEdge(id=f"{kind}:{source}:{target}", type=kind, source=source, target=target,
            evidence_ids=sorted(ids, key=str), count=len(ids))
            for (kind, source, target), ids in sorted(edges.items())],
        relationship_count=used)
