"use client";

import { useEffect, useState } from "react";
import { useAuth } from "./auth-provider";

export function PdfViewer({ id, filename }: { id: string; filename: string }) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setUrl(null); setError(null);
    void (async () => {
      try {
        const response = await fetch(`/api/documents/${id}/file`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
        });
        if (response.status === 401) { await signOut(); return; }
        if (!response.ok) throw new Error(response.status === 404 ? "The original PDF is no longer available." : "Couldn’t load this PDF. Please try again.");
        if (!response.headers.get("content-type")?.startsWith("application/pdf")) throw new Error("The file could not be opened as a PDF.");
        const blob = await response.blob();
        if (blob.size > 30 * 1024 * 1024) throw new Error("This PDF exceeds the supported size.");
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Couldn’t load this PDF.");
      }
    })();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id, token, signOut, attempt]);

  if (error) return <div className="library-error" role="alert">{error}<button className="text-button" onClick={() => setAttempt(value => value + 1)}>Retry PDF</button></div>;
  if (!url) return <p className="library-status" role="status">Loading original PDF…</p>;
  return <section className="pdf-viewer" aria-label="Original PDF viewer"><div className="pdf-toolbar"><span>Original PDF · Private</span><div><a href={url} target="_blank" rel="noopener noreferrer">Open in new tab</a><a href={url} download={filename}>Download PDF</a></div></div><iframe src={url} title={`PDF preview: ${filename}`} /><p className="pdf-viewer-help">Use your browser’s PDF controls to change pages and zoom. If the preview isn’t supported on your device, open or download the PDF above.</p></section>;
}
