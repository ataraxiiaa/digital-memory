"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth } from "./auth-provider";
import { PdfViewer } from "./pdf-viewer";

import { DocumentProcessing } from "./document-processing";
import { documentStatusLabel, type DocumentSummary, type DocumentDetail } from "../lib/documents";

const PAGE_SIZE = 20;
const MAX_BYTES = 30 * 1024 * 1024;

function displayDate(value: string) {
  return new Intl.DateTimeFormat("en", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

export function DocumentLibrary() {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [collection, setCollection] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const request = useRef<XMLHttpRequest | null>(null);
  const detailRequest = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const dialog = useRef<HTMLDialogElement>(null);

  const updateDocument = useCallback((updated: DocumentDetail) => {
    setDetail(current => current?.id === updated.id ? updated : current);
    setDocuments(current => current.map(row => row.id === updated.id ? {
      ...row, status: updated.status, processing_run_id: updated.processing_run_id,
      processing_started_at: updated.processing_started_at, processing_error: updated.processing_error,
    } : row));
  }, []);

  useEffect(() => {
    if (!token || !documents.some(document => document.status === "processing")) return;
    const controller = new AbortController();
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch(`/api/documents?limit=${PAGE_SIZE}&offset=${offset}`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
        });
        if (response.status === 401) { await signOut(); return; }
        if (response.ok && !controller.signal.aborted) setDocuments(await response.json());
      } catch { /* Manual refresh remains available when polling cannot reach the service. */ }
      finally { polling = false; }
    }, 5000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [documents, offset, token, signOut]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; request.current?.abort(); detailRequest.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setLoading(true); setListError(null); setDocuments([]);
    void fetch(`/api/documents?limit=${PAGE_SIZE}&offset=${offset}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (response.status === 401) { await signOut(); throw new Error("Your session expired. Please sign in again."); }
        if (!response.ok) throw new Error("Couldn’t load your documents. Please try again.");
        const rows = await response.json();
        if (!Array.isArray(rows)) throw new Error("Couldn’t load your documents. Please try again.");
        if (!controller.signal.aborted) setDocuments(rows);
      })
      .catch(error => { if (!controller.signal.aborted) setListError(error instanceof Error ? error.message : "Couldn’t load your documents."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [token, offset, revision, signOut]);

  useEffect(() => { if (detail && !dialog.current?.open) dialog.current?.showModal(); }, [detail]);

  function chooseFile(next: File | null) {
    setSuccess(null); setUploadError(null); setFile(null);
    if (!next) return;
    if (!next.name.toLowerCase().endsWith(".pdf") || (next.type && !["application/pdf", "application/octet-stream"].includes(next.type))) {
      setUploadError("Choose a PDF file."); return;
    }
    if (next.size > MAX_BYTES) { setUploadError("PDF files must not exceed 30 MiB."); return; }
    if (!next.size) { setUploadError("The selected file is empty."); return; }
    setFile(next); setTitle(next.name.slice(0, -4).slice(0, 300));
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !token || uploading) return;
    setUploading(true); setProgress(0); setUploadError(null); setSuccess(null);
    const data = new FormData();
    data.append("file", file);
    data.append("title", title.trim());
    data.append("collection", collection.trim() || "Unfiled");
    const xhr = new XMLHttpRequest();
    request.current = xhr;
    xhr.open("POST", "/api/documents/upload");
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.timeout = 125000;
    xhr.upload.onprogress = event => { if (mounted.current && event.lengthComputable) setProgress(Math.round(event.loaded / event.total * 100)); };
    const fail = (message: string) => { if (mounted.current) { setUploading(false); setUploadError(message); } };
    xhr.onload = () => {
      if (!mounted.current) return;
      setUploading(false);
      let result;
      try { result = JSON.parse(xhr.responseText); } catch { fail("Could not confirm the upload. Refresh your documents before retrying."); return; }
      if (xhr.status === 401) { void signOut(); fail("Your session expired. Please sign in again."); return; }
      if (xhr.status !== 201) { fail(typeof result.detail === "string" ? result.detail : "The upload failed. Please check your PDF and try again."); return; }
      setSuccess(`“${result.title}” was uploaded. You can now extract its text.`);
      setFile(null); setTitle(""); setCollection("");
      if (input.current) input.current.value = "";
      setOffset(0); setRevision(value => value + 1);
    };
    xhr.onerror = () => fail("Connection lost. Refresh your documents before retrying the upload.");
    xhr.ontimeout = () => fail("The upload timed out. Refresh your documents before retrying.");
    xhr.onabort = () => fail("Upload interrupted. Refresh your documents before retrying.");
    xhr.send(data);
  }

  async function openDocument(id: string) {
    detailRequest.current?.abort();
    const controller = new AbortController(); detailRequest.current = controller;
    setDetailLoading(true); setDetailError(null);
    try {
      const response = await fetch(`/api/documents/${id}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal });
      if (response.status === 401) { await signOut(); return; }
      if (!response.ok) throw new Error(response.status === 404 ? "This document is no longer available." : "Couldn’t open this document. Please try again.");
      const result = await response.json();
      if (!controller.signal.aborted) setDetail(result);
    } catch (error) { if (!controller.signal.aborted) setDetailError(error instanceof Error ? error.message : "Couldn’t open the document."); }
    finally { if (!controller.signal.aborted) setDetailLoading(false); }
  }

  return <>
    <section className="upload-panel" aria-labelledby="upload-heading"><div><p className="eyebrow">ADD TO YOUR COLLECTION</p><h2 id="upload-heading">Bring a document with you.</h2><p>Upload an original PDF to your private library.</p><small>PDF only · Up to 30 MiB · Password-protected PDFs aren’t supported</small></div>
      <form onSubmit={upload} aria-label="Upload PDF"><label htmlFor="pdf-file" className="file-picker">Choose a PDF<input ref={input} id="pdf-file" type="file" accept=".pdf,application/pdf" disabled={uploading} onChange={event => chooseFile(event.target.files?.[0] ?? null)} /></label>
        {file && <><p className="chosen-file">{file.name} · {(file.size / 1024 / 1024).toFixed(2)} MiB</p><div className="upload-fields"><label>Document title<input required maxLength={300} value={title} disabled={uploading} onChange={event => setTitle(event.target.value)} /></label><label>Collection (optional)<input maxLength={100} placeholder="Unfiled" value={collection} disabled={uploading} onChange={event => setCollection(event.target.value)} /></label></div></>}
        <button className="button" type="submit" disabled={!file || !title.trim() || uploading}>{uploading ? progress < 100 ? "Sending PDF…" : "Saving PDF…" : "Upload PDF"}</button>
        {uploading && <div className="upload-progress" role="status"><progress max={100} value={progress} aria-label="PDF transfer progress" /><span>{progress < 100 ? `${progress}% sent to the server` : "Transfer complete. Saving and validating your PDF…"}</span></div>}
        {uploadError && <p className="library-error" role="alert">{uploadError}</p>}{success && <p className="library-success" role="status">{success}</p>}
      </form>
    </section>
    <section className="library" aria-labelledby="library-heading"><div className="section-heading"><div><h2 id="library-heading">Your documents</h2><p>Originals you’ve added to your own collection.</p></div><button className="text-button" disabled={loading} onClick={() => setRevision(value => value + 1)}>Refresh documents</button></div>
      {loading ? <p className="library-status" role="status">Loading your documents…</p> : listError ? <div className="library-error" role="alert">{listError}<button className="text-button" onClick={() => setRevision(value => value + 1)}>Try again</button></div> : documents.length === 0 ? <div className="empty"><h2>{offset ? "No more documents." : "A fresh start."}</h2><p>{offset ? "Go back to see earlier results." : "Choose a PDF above to start your collection."}</p></div> : <div className="document-list">{documents.map(document => <button className="document-row" key={document.id} disabled={detailLoading} onClick={() => void openDocument(document.id)}><span className="document-icon" aria-hidden="true">PDF</span><span className="document-info"><span className="document-title">{document.title}</span><span className="document-meta">{document.collection} · Added {displayDate(document.created_at)}</span></span><span className="upload-state" data-status={document.status}>{documentStatusLabel[document.status]}</span><span className="row-arrow" aria-hidden="true">↗</span></button>)}</div>}
      {detailLoading && <p role="status">Opening document…</p>}{detailError && <p className="library-error" role="alert">{detailError}</p>}
      {!loading && !listError && (offset > 0 || documents.length === PAGE_SIZE) && <div className="pagination"><button className="text-button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button><span>Page {offset / PAGE_SIZE + 1}</span><button className="text-button" disabled={documents.length < PAGE_SIZE} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div>}
    </section>
    <dialog ref={dialog} className={detail?.original_filename ? "detail library-dialog pdf-document-dialog" : "detail library-dialog"} onClose={() => setDetail(null)} onClick={event => { if (event.target === event.currentTarget) { dialog.current?.close(); setDetail(null); } }} aria-labelledby="document-heading">{detail && <><div className="detail-top"><span className="sample-badge">Your document</span><button className="close" aria-label="Close document" onClick={() => dialog.current?.close()}>×</button></div><h2 id="document-heading">{detail.title}</h2><p className="subtitle">{detail.collection} · Added {displayDate(detail.created_at)}</p>{detail.original_filename && <p className="document-file-info">{detail.original_filename} · {detail.page_count} pages · {((detail.file_size_bytes ?? 0) / 1024 / 1024).toFixed(2)} MiB</p>}{detail.original_filename ? <DocumentProcessing key={`processing-${detail.id}`} document={detail} onUpdate={updateDocument} /> : <div className="document-content">{detail.content}</div>}{detail.original_filename && <PdfViewer key={`pdf-${detail.id}`} id={detail.id} filename={detail.original_filename} />}</>}</dialog>
  </>;
}
