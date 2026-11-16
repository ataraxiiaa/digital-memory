"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import "../app/entity-library.css";
import { useAuth } from "./auth-provider";
import {
  ENTITY_TYPES, kindLabel, sourceLabel, type EntityDetail, type EntityRecord, type EntityType, type Evidence, type Suggestion,
} from "../lib/entity-graph";

const PAGE = 30;

type Resolve = { new_entities: number; new_links: number; pending_suggestions: number; skipped_ambiguous: number; relinked: number };

function EvidenceList({ items, titles }: { items: Evidence[]; titles: Record<string, string> }) {
  return <ul className="entity-evidence">{items.map(item => <li key={item.mention_id}><q>{item.text.replace(/\s+/g, " ")}</q> <small>{sourceLabel(item, titles)}</small></li>)}</ul>;
}

export function EntityLibrary() {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [entities, setEntities] = useState<EntityRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [type, setType] = useState<EntityType | "">("");
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionTotal, setSuggestionTotal] = useState(0);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [rename, setRename] = useState("");
  const [mergeWith, setMergeWith] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useRef(0);

  const call = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await fetch(`/api/entities${path}`, {
      ...init, cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    });
    if (response.status === 401) { await signOut(); throw new Error("Your session expired."); }
    if (!response.ok) {
      const result = await response.json().catch(() => null);
      throw new Error(typeof result?.detail === "string" ? result.detail : "Something went wrong. Please try again.");
    }
    return response.status === 204 ? null : response.json();
  }, [token, signOut]);

  const refresh = useCallback(async () => {
    if (!token) return;
    const ticket = ++load.current;
    const list = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (type) list.set("type", type);
    if (query.trim()) list.set("q", query.trim());
    try {
      const [entityPage, suggestionPage] = await Promise.all([
        call(`?${list}`), call("/suggestions?status=pending&limit=20"),
      ]);
      if (ticket !== load.current) return;
      setEntities(entityPage.entities); setTotal(entityPage.total);
      setSuggestions(suggestionPage.suggestions); setSuggestionTotal(suggestionPage.total);
      setError(null);
    } catch (failure) {
      if (ticket === load.current) setError(failure instanceof Error ? failure.message : "Could not load entities.");
    } finally { if (ticket === load.current) setLoading(false); }
  }, [token, call, offset, type, query]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!token) return;
    void (async () => {
      try {
        const response = await fetch("/api/documents?limit=100&offset=0", { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
        if (response.ok) setTitles(Object.fromEntries((await response.json()).map((row: { id: string; title: string }) => [row.id, row.title])));
      } catch { /* Titles are a convenience; ids are shown without them. */ }
    })();
  }, [token]);

  async function act(work: () => Promise<string | void>) {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    try { const message = await work(); if (message) setNotice(message); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  const post = (path: string, body?: unknown) => call(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
  async function open(id: string) {
    try { const result: EntityDetail = await call(`/${id}`); setDetail(result); setRename(result.entity.canonical_name); setMergeWith(""); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not open that entity."); }
  }
  const reopen = async (id: string) => { await open(id); };

  const resolveAll = () => act(async () => {
    const result: Resolve = await post("/resolve");
    return `Grouped ${result.new_links} accepted mention${result.new_links === 1 ? "" : "s"} into ${result.new_entities} new entit${result.new_entities === 1 ? "y" : "ies"}. ${result.pending_suggestions} possible merge${result.pending_suggestions === 1 ? "" : "s"} to review.`
      + (result.skipped_ambiguous ? ` ${result.skipped_ambiguous} mention${result.skipped_ambiguous === 1 ? " matched" : "s matched"} several entities and ${result.skipped_ambiguous === 1 ? "was" : "were"} left unlinked.` : "");
  });

  const mergeInto = (suggestion: Suggestion, keep: "a" | "b") => act(async () => {
    const kept = suggestion[keep];
    await post(`/suggestions/${suggestion.id}/confirm`, { keep_id: kept.id });
    if (detail) setDetail(null);
    return `Merged into “${kept.canonical_name}”. You can undo this from the entity’s history.`;
  });

  const sameType = detail ? entities.filter(item => item.type === detail.entity.type && item.id !== detail.entity.id) : [];

  return <section className="entity-library" aria-label="Entities">
    <div className="processing-actions">
      <button className="text-button" disabled={busy} onClick={() => void resolveAll()}>{busy ? "Working…" : "Update entities from reviewed mentions"}</button>
      <label className="entity-retype">Type <select value={type} onChange={event => { setType(event.target.value as EntityType | ""); setOffset(0); }}>
        <option value="">All types</option>{ENTITY_TYPES.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
      <label className="entity-retype">Search <input value={query} maxLength={100} onChange={event => { setQuery(event.target.value); setOffset(0); }} /></label>
    </div>
    <p className="entity-hint">Only mentions you accepted become entities. Nothing merges until you confirm it.</p>
    {notice && <p className="library-success" role="status">{notice}</p>}
    {error && <p className="library-error" role="alert">{error}</p>}

    <div className="processing-heading"><h3>Possible merges ({suggestionTotal})</h3></div>
    {!loading && suggestions.length === 0 && <p className="entity-hint">No merges to review.</p>}
    <ul className="entity-list">{suggestions.map(item => <li key={item.id} className="entity-item" data-weak={item.score < 0.5}>
      <div className="entity-main">
        <strong>{item.a.canonical_name}</strong><span aria-hidden="true">↔</span><strong>{item.b.canonical_name}</strong>
        <span className="entity-label" data-label={item.a.type}>{item.a.type}</span>
        <span className="entity-meta">{kindLabel[item.kind]} · {Math.round(item.score * 100)}%</span>
        {item.ambiguous && <span className="entity-warning-chip">Ambiguous</span>}
      </div>
      <p className="entity-reason">{item.reason}</p>
      <div className="entity-sides"><div><small>{item.a.mention_count} mention{item.a.mention_count === 1 ? "" : "s"}</small><EvidenceList items={item.a.evidence} titles={titles} /></div>
        <div><small>{item.b.mention_count} mention{item.b.mention_count === 1 ? "" : "s"}</small><EvidenceList items={item.b.evidence} titles={titles} /></div></div>
      <div className="entity-actions">
        <button className="text-button" disabled={busy} onClick={() => void mergeInto(item, "a")}>Merge into “{item.a.canonical_name}”</button>
        <button className="text-button" disabled={busy} onClick={() => void mergeInto(item, "b")}>Merge into “{item.b.canonical_name}”</button>
        <button className="text-button" disabled={busy} onClick={() => void act(async () => { await post(`/suggestions/${item.id}/dismiss`); })}>Not the same</button>
      </div>
    </li>)}</ul>

    <div className="processing-heading"><h3>Entities ({total})</h3></div>
    {loading && <p role="status">Loading entities…</p>}
    {!loading && entities.length === 0 && <p className="entity-hint">No entities yet. Accept mentions in a document’s Entities panel, then update entities here.</p>}
    <ul className="entity-list">{entities.map(item => <li key={item.id} className="entity-item" data-review={item.status === "confirmed" ? "accepted" : "suggested"}>
      <div className="entity-main">
        <button className="text-button entity-name" onClick={() => void open(item.id)} aria-expanded={detail?.entity.id === item.id}>{item.canonical_name}</button>
        <span className="entity-label" data-label={item.type}>{item.type}</span>
        <span className="entity-meta">{item.mention_count} mention{item.mention_count === 1 ? "" : "s"} · {item.document_count} document{item.document_count === 1 ? "" : "s"}{item.status === "suggested" ? " · not yet confirmed" : ""}</span>
        {item.aliases.length > 1 && <span className="entity-note">also written: {item.aliases.filter(alias => alias !== item.canonical_name).join(", ")}</span>}
      </div>
      {detail?.entity.id === item.id && <div className="entity-detail">
        <div className="entity-actions">
          <label className="entity-retype">Name <input value={rename} maxLength={300} onChange={event => setRename(event.target.value)} /></label>
          <button className="text-button" disabled={busy || !rename.trim() || rename.trim() === detail.entity.canonical_name} onClick={() => void act(async () => { await call(`/${detail.entity.id}`, { method: "PATCH", body: JSON.stringify({ name: rename.trim() }) }); await reopen(detail.entity.id); })}>Rename</button>
          {detail.entity.status === "suggested" && <button className="text-button" disabled={busy} onClick={() => void act(async () => { await post(`/${detail.entity.id}/confirm`); await reopen(detail.entity.id); })}>Confirm entity</button>}
        </div>
        <ul className="entity-evidence">{detail.mentions.map(mention => <li key={mention.mention_id}><q>{mention.text.replace(/\s+/g, " ")}</q> <small>{sourceLabel(mention, titles)}</small>
          {detail.mentions.length > 1 && <button className="text-button" disabled={busy} onClick={() => void act(async () => { await post("/mentions/split", { run_id: mention.run_id, mention_id: mention.mention_id }); await reopen(detail.entity.id); return "Moved that mention into its own entity."; })}>Split off</button>}</li>)}</ul>
        {sameType.length > 0 && <div className="entity-actions"><label className="entity-retype">Merge another {detail.entity.type} into this one <select value={mergeWith} onChange={event => setMergeWith(event.target.value)}>
          <option value="">choose…</option>{sameType.map(other => <option key={other.id} value={other.id}>{other.canonical_name}</option>)}</select></label>
          <button className="text-button" disabled={busy || !mergeWith} onClick={() => void act(async () => { await post("/merge", { keep_id: detail.entity.id, absorb_id: mergeWith }); await reopen(detail.entity.id); return "Merged. You can undo this below."; })}>Merge</button></div>}
        {detail.merges.some(merge => !merge.undone_at) && <div className="entity-history"><small>Merge history</small><ul>{detail.merges.filter(merge => !merge.undone_at).map(merge => <li key={merge.id}>Merged in “{merge.absorbed_name}” <button className="text-button" disabled={busy} onClick={() => void act(async () => { await post(`/merges/${merge.id}/undo`); await reopen(detail.entity.id); return `Restored “${merge.absorbed_name}”.`; })}>Undo</button></li>)}</ul></div>}
      </div>}
    </li>)}</ul>
    {total > PAGE && <div className="processing-actions">
      <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
      <button className="text-button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
    </div>}
  </section>;
}
