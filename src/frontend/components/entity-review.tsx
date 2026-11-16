"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./auth-provider";
import type { DocumentDetail } from "../lib/documents";

export const ENTITY_LABELS = ["person", "organization", "project", "model", "method", "dataset", "component", "date", "event", "location"] as const;
type Label = (typeof ENTITY_LABELS)[number];
type ReviewStatus = "suggested" | "accepted" | "rejected";

type Citation = { extraction_id: string; page_number: number; region_id: string; start_char: number; end_char: number };
type Mention = {
  id: string; run_id: string; page_number: number; label: Label; text: string; score: number;
  citation: Citation; warnings: string[]; review_status: ReviewStatus; reviewed_label: Label | null;
};
type Run = {
  id: string; status: "processing" | "completed" | "needs_review" | "failed";
  label_schema_version: string | null; threshold: number | null;
  failed_passage_count: number | null; rejected_candidate_count: number | null; error: string | null;
};
type Page = {
  run: Run | null; total: number; offset: number; limit: number; mentions: Mention[];
  counts: { total: number; suggested: number; accepted: number; rejected: number; by_label: Record<string, number> };
};
type Region = { id: string; text: string };
type Extraction = { id: string; pages: { regions: Region[] }[] };
type Report = { native_extraction: Extraction; ocr_extraction: Extraction | null };

const PAGE_SIZE = 25;

function sourceContext(report: Report, citation: Citation) {
  for (const run of [report.native_extraction, report.ocr_extraction]) {
    if (!run || run.id !== citation.extraction_id) continue;
    for (const page of run.pages) {
      const region = page.regions.find(candidate => candidate.id === citation.region_id);
      if (region) {
        const from = Math.max(0, citation.start_char - 90);
        return {
          before: (from > 0 ? "…" : "") + region.text.slice(from, citation.start_char),
          match: region.text.slice(citation.start_char, citation.end_char),
          after: region.text.slice(citation.end_char, citation.end_char + 90) + (citation.end_char + 90 < region.text.length ? "…" : ""),
        };
      }
    }
  }
  return null;
}

export function EntityReview({ document }: { document: DocumentDetail }) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [data, setData] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [schema, setSchema] = useState<"full-v1" | "research-v1" | "core-v1">("full-v1");
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<ReviewStatus | "">("suggested");
  const [label, setLabel] = useState<Label | "">("");
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!token) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    if (!quiet) setLoading(true);
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (status) query.set("status", status);
    if (label) query.set("label", label);
    try {
      const response = await fetch(`/api/documents/${document.id}/entities?${query}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
      });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) throw new Error("Could not load entity suggestions. Refresh and try again.");
      const page: Page = await response.json();
      if (!controller.signal.aborted) { setData(page); setError(null); }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load entity suggestions.");
    } finally { if (!controller.signal.aborted && !quiet) setLoading(false); }
  }, [token, document.id, offset, status, label, signOut]);

  useEffect(() => { void load(); return () => request.current?.abort(); }, [load]);
  useEffect(() => { setReport(null); setOpen(null); setOffset(0); }, [document.processing_run_id]);

  useEffect(() => { if (data && data.mentions.length === 0 && offset > 0) setOffset(Math.max(0, offset - PAGE_SIZE)); }, [data, offset]);

  const running = data?.run?.status === "processing";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(true), 3000);
    return () => clearInterval(timer);
  }, [running, load]);

  async function start() {
    if (!token || starting) return;
    setStarting(true); setError(null);
    try {
      const response = await fetch(`/api/documents/${document.id}/entities?schema=${schema}`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(typeof result?.detail === "string" ? result.detail : "Could not start entity extraction.");
      }
      setOffset(0); setStatus("suggested");
      await load(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not start entity extraction.");
    } finally { setStarting(false); }
  }

  async function review(mention: Mention, next: ReviewStatus, corrected?: Label) {
    if (!token || busy) return;
    setBusy(mention.id); setError(null);
    try {
      const response = await fetch(`/api/documents/${document.id}/entities/${mention.run_id}/${mention.id}`, {
        method: "PATCH", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ status: next, label: next === "accepted" && corrected && corrected !== mention.label ? corrected : null }),
      });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) throw new Error("Could not save that review. Try again.");
      await load(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that review.");
    } finally { setBusy(null); }
  }

  async function toggleSource(mention: Mention) {
    setSourceError(null);
    if (open === mention.id) { setOpen(null); return; }
    setOpen(mention.id);
    if (report || !token) return;
    try {
      const response = await fetch(`/api/documents/${document.id}/extraction`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (!response.ok) throw new Error();
      setReport(await response.json());
    } catch { setSourceError("Could not load the source passage."); }
  }

  const counts = data?.counts;
  const run = data?.run;
  const canStart = !starting && !running;

  return <section className="entity-review" aria-label="Entity review">
    <div className="processing-heading"><h3>Entities</h3>
      {run && <span className="upload-state" data-status={run.status === "completed" ? "ready" : run.status}>{run.status === "completed" ? "Complete" : run.status === "needs_review" ? "Needs review" : run.status === "failed" ? "Failed" : "Running"}</span>}
    </div>
    {!loading && !run && <p>Find people, organizations, projects, dates, events, locations and research terms. Every result links to its exact source text and stays a suggestion until you accept it.</p>}
    {running && <p role="status">Finding entities. The first run loads a local model and can take a few minutes.</p>}
    {run?.status === "failed" && <p role="alert">{run.error ?? "Entity extraction could not finish. Please retry."}</p>}
    <div className="processing-actions">
      <label className="entity-retype">Types <select value={schema} disabled={!canStart} onChange={event => setSchema(event.target.value as typeof schema)}>
        <option value="full-v1">Everyday, research and timeline</option><option value="research-v1">Research papers</option><option value="core-v1">People, organizations, projects</option></select></label>
      <button className="text-button" disabled={!canStart} onClick={() => void start()}>{running || starting ? "Finding entities…" : run ? "Re-run entity extraction" : "Find entities"}</button>
    </div>
    {error && <p className="library-error" role="alert">{error}</p>}

    {run && counts && run.status !== "failed" && <>
      <p className="entity-summary">{counts.total} suggested · <strong>{counts.accepted}</strong> accepted · {counts.rejected} rejected · {counts.suggested} awaiting review
        {run.label_schema_version && <> · schema {run.label_schema_version}</>}
        {run.threshold != null && <> · score ≥ {run.threshold}</>}</p>
      {(run.failed_passage_count ?? 0) > 0 && <p className="library-error">{run.failed_passage_count} passages could not be processed, so this list may be incomplete.</p>}
      <div className="entity-filters">
        <label>Show <select value={status} onChange={event => { setStatus(event.target.value as ReviewStatus | ""); setOffset(0); }}>
          <option value="suggested">Awaiting review</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option><option value="">All</option></select></label>
        <label>Type <select value={label} onChange={event => { setLabel(event.target.value as Label | ""); setOffset(0); }}>
          <option value="">All types</option>{ENTITY_LABELS.map(item => <option key={item} value={item}>{item}{counts.by_label[item] ? ` (${counts.by_label[item]})` : ""}</option>)}</select></label>
      </div>
      {loading && <p role="status">Loading suggestions…</p>}
      {!loading && data && data.mentions.length === 0 && <p>No suggestions match these filters.</p>}
      <ul className="entity-list">
        {data?.mentions.map(mention => {
          const effective = mention.reviewed_label ?? mention.label;
          const context = open === mention.id && report ? sourceContext(report, mention.citation) : null;
          return <li key={mention.id} className="entity-item" data-review={mention.review_status}>
            <div className="entity-main">
              <span className="entity-text">{mention.text.replace(/\s+/g, " ")}</span>
              <span className="entity-label" data-label={effective}>{effective}</span>
              {mention.reviewed_label && <span className="entity-note">model said {mention.label}</span>}
              <span className="entity-meta">page {mention.page_number} · score {mention.score.toFixed(2)}</span>
            </div>
            {mention.warnings.length > 0 && <p className="entity-warning">{[...new Set(mention.warnings)].join(" · ")}</p>}
            <div className="entity-actions">
              <button className="text-button" disabled={busy === mention.id || mention.review_status === "accepted"} onClick={() => void review(mention, "accepted")}>Accept</button>
              <button className="text-button" disabled={busy === mention.id || mention.review_status === "rejected"} onClick={() => void review(mention, "rejected")}>Reject</button>
              {mention.review_status !== "suggested" && <button className="text-button" disabled={busy === mention.id} onClick={() => void review(mention, "suggested")}>Undo</button>}
              <label className="entity-retype">Accept as <select aria-label={`Accept “${mention.text}” as another type`} value="" disabled={busy === mention.id} onChange={event => { if (event.target.value) void review(mention, "accepted", event.target.value as Label); }}>
                <option value="">type…</option>{ENTITY_LABELS.filter(item => item !== effective).map(item => <option key={item} value={item}>{item}</option>)}</select></label>
              <button className="text-button" aria-expanded={open === mention.id} onClick={() => void toggleSource(mention)}>{open === mention.id ? "Hide source" : "Show source"}</button>
            </div>
            {open === mention.id && <p className="entity-source">{context ? <>{context.before}<mark>{context.match}</mark>{context.after}</> : sourceError ?? "Loading source…"}</p>}
          </li>;
        })}
      </ul>
      {data && data.total > PAGE_SIZE && <div className="processing-actions">
        <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
        <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}</span>
        <button className="text-button" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
      </div>}
    </>}
  </section>;
}
