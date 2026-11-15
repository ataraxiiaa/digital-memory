"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "./auth-provider";
import { documentStatusLabel, type DocumentDetail } from "../lib/documents";

type ExtractedPage = { page_number: number; warnings: string[]; regions: { text: string }[] };
type ExtractionReport = {
  native_extraction: { pages: ExtractedPage[] };
  ocr_extraction: { pages: ExtractedPage[] } | null;
  pages: { page_number: number; warnings: string[] }[];
  tables: { page_number: number; warnings: string[] }[];
  warnings: string[];
};

export function DocumentProcessing({ document, onUpdate }: {
  document: DocumentDetail;
  onUpdate: (document: DocumentDetail) => void;
}) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ExtractionReport | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [pageIndex, setPageIndex] = useState(0);
  const action = useRef<AbortController | null>(null);
  const resultRequest = useRef<AbortController | null>(null);
  const started = document.processing_started_at ? Date.parse(document.processing_started_at) : 0;
  const stale = document.status === "processing" && (!started || Date.now() - started >= 20 * 60 * 1000);

  useEffect(() => () => { action.current?.abort(); resultRequest.current?.abort(); }, []);
  useEffect(() => { setReport(null); setPageIndex(0); }, [document.processing_run_id]);

  useEffect(() => {
    if (!token || (!running && document.status !== "processing")) return;
    const controller = new AbortController();
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch(`/api/documents/${document.id}`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
        });
        if (response.status === 401) { await signOut(); return; }
        if (response.ok && !controller.signal.aborted) onUpdate(await response.json());
      } catch { /* The action response or a manual refresh reports connectivity failures. */ }
      finally { polling = false; }
    }, 3000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [document.id, document.status, running, token, onUpdate, signOut]);

  async function processDocument() {
    if (!token || running) return;
    const controller = new AbortController(); action.current = controller;
    setRunning(true); setError(null); setReport(null);
    try {
      const response = await fetch(`/api/documents/${document.id}/process`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(310000)]),
      });
      if (response.status === 401) { await signOut(); return; }
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Could not confirm extraction. Refresh the document status.");
      if (!controller.signal.aborted) onUpdate(result);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not confirm extraction. Refresh the document status.");
    } finally {
      if (!controller.signal.aborted) {
        try {
          const latest = await fetch(`/api/documents/${document.id}`, {
            headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
          });
          if (latest.ok && !controller.signal.aborted) onUpdate(await latest.json());
        } catch { /* A later refresh can confirm an ambiguous processing result. */ }
        setRunning(false);
      }
    }
  }

  async function loadReport() {
    if (!token || loadingReport) return;
    const controller = new AbortController(); resultRequest.current = controller;
    setLoadingReport(true); setError(null);
    try {
      const response = await fetch(`/api/documents/${document.id}/extraction`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
      });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) throw new Error("Could not load extraction results. Refresh the document and try again.");
      if (!controller.signal.aborted) setReport(await response.json());
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load extraction results.");
    } finally { if (!controller.signal.aborted) setLoadingReport(false); }
  }

  const page = report?.native_extraction.pages[pageIndex];
  const ocrPage = report?.ocr_extraction?.pages.find(candidate => candidate.page_number === page?.page_number);
  const notes = page && report ? [...new Set([
    ...report.warnings, ...page.warnings,
    ...(report.pages.find(candidate => candidate.page_number === page.page_number)?.warnings ?? []),
    ...(ocrPage?.warnings ?? []),
    ...report.tables.filter(table => table.page_number === page.page_number).flatMap(table => table.warnings),
  ])] : [];

  return <section className="processing-panel" aria-label="Document extraction">
    <div className="processing-heading"><h3>Extraction</h3><span className="upload-state" data-status={document.status}>{documentStatusLabel[document.status]}</span></div>
    {document.status === "uploaded" && <p>Your PDF is stored. Extract it to read its text and identify tables.</p>}
    {(running || document.status === "processing") && <p role="status">Extraction may take a few minutes. You can close this view and refresh the document status later.</p>}
    {document.status === "ready" && <p>Extraction finished. The text is available below.</p>}
    {document.status === "needs_review" && <p>Extraction finished with content that needs checking against the original.</p>}
    {document.status === "failed" && <p role="alert">{document.processing_error ?? "Extraction could not finish. Please retry."}</p>}
    {stale && <p>This attempt is taking longer than expected. You can retry it.</p>}
    <div className="processing-actions"><button className="text-button" disabled={running || (document.status === "processing" && !stale)} onClick={() => void processDocument()}>{running || (document.status === "processing" && !stale) ? "Extracting…" : document.status === "uploaded" ? "Extract document" : "Retry extraction"}</button>
      {(document.status === "ready" || document.status === "needs_review") && <button className="text-button" disabled={loadingReport} onClick={() => void loadReport()}>{loadingReport ? "Loading results…" : "View extracted text"}</button>}
    </div>
    {error && <p className="library-error" role="alert">{error}</p>}
    {report && page && <div className="extraction-results">
      <p>{report.native_extraction.pages.length} pages · {report.tables.length} tables · {report.ocr_extraction?.pages.reduce((sum, item) => sum + item.regions.length, 0) ?? 0} recognized image-text lines</p>
      <div className="processing-actions"><button className="text-button" disabled={pageIndex === 0} onClick={() => setPageIndex(index => index - 1)}>Previous extracted page</button><span>Page {page.page_number}</span><button className="text-button" disabled={pageIndex >= report.native_extraction.pages.length - 1} onClick={() => setPageIndex(index => index + 1)}>Next extracted page</button></div>
      {notes.length > 0 && <details><summary>Review notes ({notes.length})</summary><ul>{notes.map(note => <li key={note}>{note}</li>)}</ul></details>}
      <h4>PDF text</h4><div className="document-content">{page.regions.map(region => region.text).join("\n\n") || "No native text on this page."}</div>
      {(ocrPage?.regions.length ?? 0) > 0 && <><h4>Recognized image text</h4><div className="document-content">{ocrPage?.regions.map(region => region.text).join("\n\n")}</div></>}
    </div>}
  </section>;
}
