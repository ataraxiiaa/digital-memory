"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./auth-provider";

export const RELATION_TYPES = ["works_at", "member_of", "located_in", "attended", "part_of", "collaborated_with"] as const;
type Kind = (typeof RELATION_TYPES)[number];

const LABELS: Record<Kind, string> = {
  works_at: "works at", member_of: "is a member of", located_in: "is located in",
  attended: "attended", part_of: "is part of", collaborated_with: "collaborated with",
};
// Which mention types each relation fits (the backend is authoritative; this only trims the menu).
const FITS: Record<Kind, [string[], string[]]> = {
  works_at: [["person"], ["organization"]],
  member_of: [["person"], ["organization", "project"]],
  located_in: [["organization", "event", "project"], ["location"]],
  attended: [["person"], ["event"]],
  part_of: [["component", "model", "organization"], ["model", "project", "organization"]],
  collaborated_with: [["person"], ["person"]],
};
const fits = (kind: Kind, subject: string, object: string) => FITS[kind][0].includes(subject) && FITS[kind][1].includes(object);

type End = { run_id: string; mention_id: string; label: string; text: string; citation: { start_char: number; end_char: number } };
type Relationship = {
  id: string; type: Kind; subject: End; object: End; page_number: number; sentence_text: string;
  sentence_start: number; score: number; review_status: "suggested" | "confirmed" | "rejected";
  corrected_type: Kind | null; swapped: boolean; document_title: string | null;
};
type Queue = { total: number; offset: number; limit: number; relationships: Relationship[] };
type Node = { id: string; name: string; type: string; mention_count: number };
type Edge = { id: string; type: Kind; source: string; target: string; evidence_ids: string[]; count: number };
type GraphData = { nodes: Node[]; edges: Edge[]; relationship_count: number };

const PAGE = 10;
const clip = (value: string, size: number) => (value.length > size ? `${value.slice(0, size - 1)}…` : value);

function highlighted(item: Relationship) {
  const spans = [
    { start: item.subject.citation.start_char - item.sentence_start, end: item.subject.citation.end_char - item.sentence_start, role: "subject" },
    { start: item.object.citation.start_char - item.sentence_start, end: item.object.citation.end_char - item.sentence_start, role: "object" },
  ].sort((a, b) => a.start - b.start);
  const parts: { text: string; role?: string }[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor || span.end > item.sentence_text.length) continue;
    if (span.start > cursor) parts.push({ text: item.sentence_text.slice(cursor, span.start) });
    parts.push({ text: item.sentence_text.slice(span.start, span.end), role: span.role });
    cursor = span.end;
  }
  parts.push({ text: item.sentence_text.slice(cursor) });
  return parts;
}

function GraphDrawing({ graph, focus, onFocus }: { graph: GraphData; focus: string | null; onFocus: (id: string | null) => void }) {
  const size = 560, center = size / 2, radius = Math.max(60, Math.min(size / 2 - 95, 28 + graph.nodes.length * 14));
  const places = useMemo(() => new Map(graph.nodes.map((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(graph.nodes.length, 1) - Math.PI / 2;
    return [node.id, { x: center + radius * Math.cos(angle), y: center + radius * Math.sin(angle), angle }];
  })), [graph.nodes, center, radius]);
  const linked = (edge: Edge) => focus === null || edge.source === focus || edge.target === focus;
  return <svg className="graph-svg" viewBox={`0 0 ${size} ${size}`} role="img"
      aria-label={`Relationship graph with ${graph.nodes.length} entities and ${graph.edges.length} confirmed links. The list below has the same information.`}>
    <defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0L10 5L0 10z" fill="currentColor" /></marker></defs>
    {graph.edges.map(edge => {
      const a = places.get(edge.source), b = places.get(edge.target);
      if (!a || !b) return null;
      const symmetric = edge.type === "collaborated_with";
      return <line key={edge.id} className="graph-edge" data-dim={!linked(edge)} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
        markerEnd={symmetric ? undefined : "url(#graph-arrow)"} strokeWidth={Math.min(1.5 + edge.count, 5)}>
        <title>{`${LABELS[edge.type]} (${edge.count} source${edge.count === 1 ? "" : "s"})`}</title></line>;
    })}
    {graph.nodes.map(node => {
      const spot = places.get(node.id)!;
      const right = Math.cos(spot.angle) >= 0;
      return <g key={node.id} className="graph-node" data-type={node.type} data-dim={focus !== null && focus !== node.id && !graph.edges.some(e => linked(e) && (e.source === node.id || e.target === node.id))}
        tabIndex={0} role="button" aria-pressed={focus === node.id} aria-label={`${node.name}, ${node.type}`}
        onClick={() => onFocus(focus === node.id ? null : node.id)}
        onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onFocus(focus === node.id ? null : node.id); } }}>
        <circle cx={spot.x} cy={spot.y} r={9} />
        <text x={spot.x + (right ? 14 : -14)} y={spot.y + 4} textAnchor={right ? "start" : "end"}>{clip(node.name, 18)}</text>
      </g>;
    })}
  </svg>;
}

export function GraphView() {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [queue, setQueue] = useState<Queue | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const load = useRef(0);

  const call = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await fetch(path, { ...init, cache: "no-store", headers: {
      Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) } });
    if (response.status === 401) { await signOut(); throw new Error("Your session expired."); }
    if (!response.ok) {
      const result = await response.json().catch(() => null);
      throw new Error(typeof result?.detail === "string" ? result.detail : "Something went wrong. Please try again.");
    }
    return response.json();
  }, [token, signOut]);

  const refresh = useCallback(async () => {
    if (!token) return;
    const ticket = ++load.current;
    try {
      const [suggested, drawn] = await Promise.all([
        call(`/api/relationships?status=suggested&limit=${PAGE}&offset=${offset}`), call("/api/graph")]);
      if (ticket !== load.current) return;
      setQueue(suggested); setGraph(drawn); setError(null);
    } catch (failure) {
      if (ticket === load.current) setError(failure instanceof Error ? failure.message : "Could not load relationships.");
    } finally { if (ticket === load.current) setLoading(false); }
  }, [token, call, offset]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (queue && queue.relationships.length === 0 && offset > 0) setOffset(Math.max(0, offset - PAGE)); }, [queue, offset]);

  async function build() {
    if (building) return;
    setBuilding(true); setError(null); setNotice(null);
    try {
      const result = await call("/api/relationships/build", { method: "POST" });
      setNotice(`Checked ${result.documents} document${result.documents === 1 ? "" : "s"}: ${result.candidates} possible relationship${result.candidates === 1 ? "" : "s"}.`
        + (result.filtered ? ` ${result.filtered} negated, hedged, or unclear statement${result.filtered === 1 ? " was" : "s were"} skipped.` : ""));
      setOffset(0);
      await refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not find relationships."); }
    finally { setBuilding(false); }
  }

  async function review(item: Relationship, body: { status: string; type?: Kind; swap?: boolean }) {
    if (busy) return;
    setBusy(item.id); setError(null); setNotice(null);
    try { await call(`/api/relationships/${item.id}`, { method: "PATCH", body: JSON.stringify(body) }); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not save that review."); }
    finally { setBusy(null); }
  }

  const names = useMemo(() => new Map((graph?.nodes ?? []).map(node => [node.id, node.name])), [graph]);

  return <section className="graph-view" aria-label="Relationships">
    <div className="graph-heading">
      <p>Relationships are suggestions between entities you have accepted, found in a single sentence. Only the ones you confirm reach the graph.</p>
      <button className="text-button" disabled={building} onClick={() => void build()}>{building ? "Finding relationships…" : "Find relationships"}</button>
    </div>
    {notice && <p className="library-success" role="status">{notice}</p>}
    {error && <p className="library-error" role="alert">{error}</p>}
    {loading && <p role="status">Loading relationships…</p>}

    <h2 className="graph-title">Review queue{queue ? ` (${queue.total})` : ""}</h2>
    {!loading && queue && queue.total === 0 && <p>No relationships are waiting. Accept entities in your documents, then choose Find relationships.</p>}
    <ul className="relation-list">
      {queue?.relationships.map(item => {
        const options = RELATION_TYPES.filter(kind => kind !== item.type && fits(kind, item.subject.label, item.object.label));
        const canSwap = fits(item.type, item.object.label, item.subject.label);
        return <li key={item.id} className="relation-item">
          <div className="relation-main">
            <strong>{item.subject.text.replace(/\s+/g, " ")}</strong>
            <span className="relation-type">{LABELS[item.type]}</span>
            <strong>{item.object.text.replace(/\s+/g, " ")}</strong>
            <span className="entity-meta">{item.document_title ? `${item.document_title} · ` : ""}page {item.page_number} · score {item.score.toFixed(2)}</span>
          </div>
          <p className="relation-sentence">{highlighted(item).map((part, index) => part.role
            ? <mark key={index} data-role={part.role}>{part.text}</mark> : <span key={index}>{part.text}</span>)}</p>
          <div className="entity-actions">
            <button className="text-button" disabled={busy === item.id} onClick={() => void review(item, { status: "confirmed" })}>Confirm</button>
            <button className="text-button" disabled={busy === item.id} onClick={() => void review(item, { status: "rejected" })}>Reject</button>
            {canSwap && <button className="text-button" disabled={busy === item.id} onClick={() => void review(item, { status: "confirmed", swap: true })}>Confirm, reversed</button>}
            {options.length > 0 && <label className="entity-retype">Confirm as <select aria-label={`Confirm “${item.subject.text} ${LABELS[item.type]} ${item.object.text}” as another type`} value="" disabled={busy === item.id}
              onChange={event => { if (event.target.value) void review(item, { status: "confirmed", type: event.target.value as Kind }); }}>
              <option value="">type…</option>{options.map(kind => <option key={kind} value={kind}>{LABELS[kind]}</option>)}</select></label>}
          </div>
        </li>;
      })}
    </ul>
    {queue && queue.total > PAGE && <div className="processing-actions">
      <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + PAGE, queue.total)} of {queue.total}</span>
      <button className="text-button" disabled={offset + PAGE >= queue.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
    </div>}

    <h2 className="graph-title">Graph of confirmed relationships</h2>
    {graph && graph.edges.length === 0 && !loading && <p>Nothing confirmed yet. Confirmed relationships appear here.</p>}
    {graph && graph.edges.length > 0 && <>
      <GraphDrawing graph={graph} focus={focus} onFocus={setFocus} />
      <p className="entity-meta">{graph.nodes.length} entities · {graph.edges.length} links from {graph.relationship_count} confirmed relationship{graph.relationship_count === 1 ? "" : "s"}. Select a node to highlight its links.</p>
      <ul className="edge-list" aria-label="Confirmed relationships">
        {graph.edges.filter(edge => focus === null || edge.source === focus || edge.target === focus).map(edge => <li key={edge.id}>
          <strong>{names.get(edge.source)}</strong> {LABELS[edge.type]} <strong>{names.get(edge.target)}</strong>
          <span className="entity-meta"> · {edge.count} source{edge.count === 1 ? "" : "s"}</span></li>)}
      </ul>
    </>}
  </section>;
}
