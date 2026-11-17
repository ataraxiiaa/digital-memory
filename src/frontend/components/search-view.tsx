"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import "../app/search.css";
import { useAuth } from "./auth-provider";
import { kindLabel, typeLabel, type Hit, type IndexStatus, type SearchResponse, type SearchType, type Segment } from "../lib/search";

const PAGE = 20;

// Segments are rendered as React text, so source text can never inject markup.
function Snippet({ segments }: { segments: Segment[] }) {
  return <p className="search-snippet">{segments.map((segment, index) =>
    segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}</p>;
}

export function SearchView({ onOpenDocument }: { onOpenDocument: (id: string) => void }) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [type, setType] = useState<SearchType>("all");
  const [documentId, setDocumentId] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  const loadStatus = useCallback(async () => {
    if (!token) return;
    try {
      const response = await fetch("/api/search/status", { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) throw new Error("Couldn’t check the search index.");
      setStatus(await response.json()); setStatusError(null);
    } catch (failure) { setStatusError(failure instanceof Error ? failure.message : "Couldn’t check the search index."); }
  }, [token, signOut]);
  useEffect(() => { void loadStatus(); }, [loadStatus]);

  useEffect(() => {
    if (!token || !submitted) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setLoading(true); setError(null);
    const query = new URLSearchParams({ q: submitted, type, limit: String(PAGE), offset: String(offset) });
    if (documentId && type !== "entities" && type !== "timeline") query.set("document_id", documentId);
    void (async () => {
      try {
        const response = await fetch(`/api/search?${query}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal });
        if (response.status === 401) { await signOut(); return; }
        if (!response.ok) {
          const result = await response.json().catch(() => null);
          throw new Error(typeof result?.detail === "string" ? result.detail : "Couldn’t search right now. Please try again.");
        }
        const result: SearchResponse = await response.json();
        if (!controller.signal.aborted) setData(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Couldn’t search right now.");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [token, submitted, type, documentId, offset, signOut]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) { setError("Enter something to search for."); setData(null); setSubmitted(""); return; }
    setOffset(0); setSubmitted(trimmed);
  }

  async function updateIndex() {
    if (!token || indexing) return;
    setIndexing(true); setNotice(null); setError(null);
    try {
      const response = await fetch("/api/search/index", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(typeof result?.detail === "string" ? result.detail : "Couldn’t update the search index.");
      }
      const result = await response.json();
      setNotice(`Indexed ${result.passages} passages from ${result.documents} document${result.documents === 1 ? "" : "s"}.`);
      await loadStatus();
      if (submitted) setOffset(0);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Couldn’t update the search index."); }
    finally { setIndexing(false); }
  }

  const behind = status ? status.stale + status.missing : 0;
  const indexable = status ? status.documents.filter(item => item.status !== "not_ready") : [];
  const total = data ? Math.max(0, ...Object.values(data.totals)) : 0;
  const open = (hit: Hit) => hit.document_id
    ? <button className="text-button" onClick={() => onOpenDocument(hit.document_id!)}>Open {hit.document_title ?? "document"}{hit.page_number ? `, page ${hit.page_number}` : ""}</button> : null;

  return <section className="search-view" aria-label="Search">
    <form className="search-form" role="search" onSubmit={submit}>
      <input aria-label="Search your documents" type="search" maxLength={200} value={input} placeholder="Search passages, people, places and moments" onChange={event => setInput(event.target.value)} />
      <button className="text-button" type="submit" disabled={loading}>{loading ? "Searching…" : "Search"}</button>
    </form>
    <div className="search-filters">
      <label>Look in <select value={type} onChange={event => { setType(event.target.value as SearchType); setOffset(0); }}>
        {(Object.keys(typeLabel) as SearchType[]).map(item => <option key={item} value={item}>{typeLabel[item]}</option>)}</select></label>
      {(type === "all" || type === "passages") && <label>Document <select value={documentId} onChange={event => { setDocumentId(event.target.value); setOffset(0); }}>
        <option value="">All documents</option>{indexable.map(item => <option key={item.document_id} value={item.document_id}>{item.title}</option>)}</select></label>}
    </div>
    <p className="search-hint">Use quotes for an exact phrase and a minus sign to leave a word out, for example <code>“budget review” -draft</code>. Search matches words and their forms, not meanings.</p>

    {statusError && <p className="library-error" role="alert">{statusError}</p>}
    {status && behind > 0 && <div className="search-banner" role="status">
      <span>{status.missing > 0 && `${status.missing} document${status.missing === 1 ? " isn’t" : "s aren’t"} searchable yet. `}
        {status.stale > 0 && `${status.stale} ${status.stale === 1 ? "has" : "have"} changed since indexing. `}Results for these may be missing or out of date.</span>
      <button className="text-button" disabled={indexing} onClick={() => void updateIndex()}>{indexing ? "Updating…" : "Update search index"}</button>
    </div>}
    {status && behind === 0 && status.current > 0 && <p className="search-fresh">Search is up to date for {status.current} document{status.current === 1 ? "" : "s"}.{" "}
      <button className="text-button" disabled={indexing} onClick={() => void updateIndex()}>{indexing ? "Updating…" : "Rebuild"}</button></p>}
    {status && status.not_ready > 0 && <p className="search-hint">{status.not_ready} document{status.not_ready === 1 ? " needs" : "s need"} extraction before {status.not_ready === 1 ? "it" : "they"} can be searched.</p>}
    {status && status.documents.length === 0 && <p className="search-hint">Add a document or note first. Search only covers what you add.</p>}
    {notice && <p className="library-success" role="status">{notice}</p>}
    {error && <p className="library-error" role="alert">{error}</p>}

    {loading && <p role="status">Searching…</p>}
    {!loading && data && !error && data.results.length === 0 && <p className="search-empty">No matches for “{data.query}”. Try fewer or different words{behind > 0 ? ", or update the search index" : ""}.</p>}
    {data && data.results.length > 0 && <>
      <p className="search-summary">{Object.entries(data.totals).filter(([, count]) => count > 0).map(([kind, count]) => `${count} ${kind}`).join(" · ")}</p>
      <ul className="search-results">
        {data.results.map((hit, index) => <li key={`${hit.kind}-${hit.timeline_item_id ?? hit.mention_id ?? hit.entity_id ?? index}-${hit.citation?.start_char ?? ""}-${index}`} className="search-hit" data-kind={hit.kind}>
          <div className="search-hit-top">
            <span className="search-kind">{kindLabel[hit.kind]}</span>
            {hit.entity_type && <span className="entity-label" data-label={hit.entity_type}>{hit.entity_type}</span>}
            {hit.kind === "entity" && hit.mention_count != null && <span className="entity-meta">{hit.mention_count} mention{hit.mention_count === 1 ? "" : "s"}</span>}
            {hit.kind === "timeline" && (hit.date_text || hit.start_date) && <span className="entity-meta">{hit.date_text ?? hit.start_date}</span>}
            {hit.review_required && <span className="entity-meta">check against the original</span>}
            {hit.kind === "timeline" && hit.review_status === "suggested" && <span className="entity-meta">unreviewed</span>}
          </div>
          <Snippet segments={hit.snippet} />
          {open(hit)}
        </li>)}
      </ul>
      {total > PAGE && <div className="processing-actions">
        <button className="text-button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <span>Page {Math.floor(offset / PAGE) + 1}</span>
        <button className="text-button" disabled={offset + PAGE >= total || loading} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>}
    </>}
  </section>;
}
