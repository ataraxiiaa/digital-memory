"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import "../app/timeline.css";
import { useAuth } from "./auth-provider";
import {
  effective, flagLabel, formatDate, highlightParts, noDateReason,
  type Precision, type ReviewStatus, type TimelineItem, type TimelinePage,
} from "../lib/timeline";

const PAGE = 50;
const PRECISIONS: Precision[] = ["day", "week", "month", "quarter", "year"];

type Build = { documents: number; items: number; undated: number; unattached_dates: number };

function Card({ item, busy, onReview }: {
  item: TimelineItem; busy: boolean;
  onReview: (item: TimelineItem, body: Record<string, unknown>) => void;
}) {
  const view = effective(item);
  const [showSource, setShowSource] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(view.title);
  const [start, setStart] = useState(view.start ?? "");
  const [end, setEnd] = useState(view.end ?? "");
  const [precision, setPrecision] = useState<Precision>(view.precision ?? "day");
  const places = item.refs.filter(ref => ref.role === "location");
  const people = item.refs.filter(ref => ref.role === "participant");
  const corrected = item.corrected_start !== null || item.corrected_title !== null;

  function save() {
    const body: Record<string, unknown> = { status: "confirmed" };
    if (title.trim() && title.trim() !== item.title) body.title = title.trim();
    if (start) { body.start = start; body.end = end || start; body.precision = precision; }
    onReview(item, body);
    setEditing(false);
  }

  return <li className="timeline-card" data-review={item.review_status}>
    <div className="timeline-date">{formatDate(view.start, view.end, view.precision)}{view.start === null && <small>{noDateReason[item.date_status]}</small>}</div>
    <div className="timeline-body">
      <div className="timeline-title">{view.title}
        {item.review_status === "confirmed" && <span className="timeline-badge" data-kind="ok">Confirmed{corrected ? " · edited" : ""}</span>}
        {item.review_status === "rejected" && <span className="timeline-badge">Rejected</span>}
        {item.stale && <span className="timeline-badge" data-kind="warn">Source changed</span>}
      </div>
      {(places.length > 0 || people.length > 0) && <div className="timeline-chips">
        {places.map(ref => <span key={ref.mention_id} className="timeline-chip" data-role="location">{ref.text.replace(/\s+/g, " ")}</span>)}
        {people.map(ref => <span key={ref.mention_id} className="timeline-chip">{ref.text.replace(/\s+/g, " ")}</span>)}
      </div>}
      {item.flags.length > 0 && <ul className="timeline-flags">{item.flags.map(flag => <li key={flag}>{flagLabel[flag] ?? flag}</li>)}</ul>}
      <p className="timeline-meta">{item.document_title ?? "Document"} · page {item.page_number}</p>
      {editing && <form className="timeline-edit" onSubmit={event => { event.preventDefault(); save(); }}>
        <label>Title<input value={title} onChange={event => setTitle(event.target.value)} maxLength={300} required /></label>
        <label>Starts<input type="date" value={start} onChange={event => setStart(event.target.value)} /></label>
        <label>Ends<input type="date" value={end} min={start || undefined} onChange={event => setEnd(event.target.value)} /></label>
        <label>Precision<select value={precision} onChange={event => setPrecision(event.target.value as Precision)}>
          {PRECISIONS.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <div className="timeline-actions"><button className="text-button" type="submit" disabled={busy}>Save and confirm</button>
          <button className="text-button" type="button" onClick={() => setEditing(false)}>Cancel</button></div>
      </form>}
      <div className="timeline-actions">
        {item.review_status !== "confirmed" && <button className="text-button" disabled={busy} onClick={() => onReview(item, { status: "confirmed" })}>Confirm</button>}
        {item.review_status !== "rejected" && <button className="text-button" disabled={busy} onClick={() => onReview(item, { status: "rejected" })}>Reject</button>}
        {item.review_status !== "suggested" && <button className="text-button" disabled={busy} onClick={() => onReview(item, { status: "suggested" })}>Undo</button>}
        <button className="text-button" disabled={busy} onClick={() => setEditing(open => !open)}>{view.start ? "Edit" : "Add a date"}</button>
        <button className="text-button" aria-expanded={showSource} onClick={() => setShowSource(open => !open)}>{showSource ? "Hide source" : "Show source"}</button>
      </div>
      {showSource && <p className="timeline-source">{highlightParts(item).map((part, index) =>
        part.role ? <mark key={index} data-role={part.role}>{part.text}</mark> : <span key={index}>{part.text}</span>)}</p>}
    </div>
  </li>;
}

export function TimelineView() {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [dated, setDated] = useState<TimelinePage | null>(null);
  const [undated, setUndated] = useState<TimelinePage | null>(null);
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useRef(0);

  const call = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await fetch(`/api/timeline${path}`, {
      ...init, cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    });
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
    const filters = new URLSearchParams();
    if (status) filters.set("status", status);
    if (after) filters.set("after", after);
    if (before) filters.set("before", before);
    try {
      const [datedPage, undatedPage] = await Promise.all([
        call(`?${new URLSearchParams({ ...Object.fromEntries(filters), dated: "true", limit: String(PAGE), offset: String(offset) })}`),
        // Date filters only make sense for dated items; the undated lane keeps status only.
        call(`?${new URLSearchParams({ ...(status ? { status } : {}), dated: "false", limit: "100" })}`),
      ]);
      if (ticket !== load.current) return;
      setDated(datedPage); setUndated(undatedPage); setError(null);
    } catch (failure) {
      if (ticket === load.current) setError(failure instanceof Error ? failure.message : "Could not load the timeline.");
    } finally { if (ticket === load.current) setLoading(false); }
  }, [token, call, status, after, before, offset]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function act(work: () => Promise<string | void>) {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    try { const message = await work(); if (message) setNotice(message); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  const build = () => act(async () => {
    const result: Build = await call("/build", { method: "POST" });
    return `Updated ${result.documents} document${result.documents === 1 ? "" : "s"}: ${result.items} timeline suggestion${result.items === 1 ? "" : "s"}, ${result.undated} still need a date.`
      + (result.unattached_dates ? ` ${result.unattached_dates} accepted date${result.unattached_dates === 1 ? " has" : "s have"} no event in the same sentence.` : "");
  });
  const review = (item: TimelineItem, body: Record<string, unknown>) =>
    act(async () => { await call(`/${item.id}`, { method: "PATCH", body: JSON.stringify(body) }); });

  const groups: { year: string; items: TimelineItem[] }[] = [];
  for (const item of dated?.items ?? []) {
    const year = (item.corrected_start ?? item.start_date ?? "").slice(0, 4);
    const last = groups[groups.length - 1];
    if (last && last.year === year) last.items.push(item); else groups.push({ year, items: [item] });
  }
  const empty = !loading && dated?.total === 0 && undated?.total === 0;

  return <section className="timeline-view" aria-label="Timeline">
    <div className="processing-actions">
      <button className="text-button" disabled={busy} onClick={() => void build()}>{busy ? "Working…" : "Update from reviewed entities"}</button>
      <label>Show <select value={status} onChange={event => { setStatus(event.target.value as ReviewStatus | ""); setOffset(0); }}>
        <option value="">All except rejected</option><option value="suggested">Needs review</option>
        <option value="confirmed">Confirmed</option><option value="rejected">Rejected</option></select></label>
      <label>From <input type="date" value={after} max={before || undefined} onChange={event => { setAfter(event.target.value); setOffset(0); }} /></label>
      <label>To <input type="date" value={before} min={after || undefined} onChange={event => { setBefore(event.target.value); setOffset(0); }} /></label>
    </div>
    {notice && <p className="library-success" role="status">{notice}</p>}
    {error && <p className="library-error" role="alert">{error}</p>}
    {loading && <p role="status">Loading your timeline…</p>}
    {empty && <div className="empty"><h2>Your story starts here.</h2>
      <p>The timeline is built from entities you have accepted. Open a document, find and accept its events, dates and places, then choose Update from reviewed entities.</p></div>}
    {groups.map(group => <div key={group.year} className="timeline-year"><h3>{group.year}</h3>
      <ol className="timeline-list">{group.items.map(item => <Card key={item.id} item={item} busy={busy} onReview={review} />)}</ol></div>)}
    {dated && dated.total > PAGE && <div className="processing-actions">
      <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + PAGE, dated.total)} of {dated.total}</span>
      <button className="text-button" disabled={offset + PAGE >= dated.total} onClick={() => setOffset(offset + PAGE)}>Next</button></div>}
    {undated && undated.total > 0 && <div className="timeline-year timeline-undated"><h3>Needs a date ({undated.total})</h3>
      <p>These events have no usable date. Add one, or leave them out.</p>
      <ol className="timeline-list">{undated.items.map(item => <Card key={item.id} item={item} busy={busy} onReview={review} />)}</ol></div>}
  </section>;
}
