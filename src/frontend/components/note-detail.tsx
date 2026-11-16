"use client";

import { useState } from "react";
import { useAuth } from "./auth-provider";
import { EntityReview } from "./entity-review";
import type { DocumentDetail } from "../lib/documents";

/** A saved plain-text note: its exact text, and entity review once it is prepared. */
export function NoteDetail({ document, onUpdate }: { document: DocumentDetail; onUpdate: (document: DocumentDetail) => void }) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prepared = Boolean(document.processing_run_id);

  async function prepare() {
    if (!token || preparing) return;
    setPreparing(true); setError(null);
    try {
      const response = await fetch(`/api/documents/${document.id}/process-text`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (response.status === 401) { await signOut(); return; }
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : "Could not prepare the note. Try again.");
      onUpdate(result);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not prepare the note. Try again.");
    } finally { setPreparing(false); }
  }

  return <>
    <div className="document-content note-text">{document.content}</div>
    {prepared ? <EntityReview document={document} /> : <section className="entity-review" aria-label="Note preparation">
      <div className="processing-heading"><h3>Entities</h3></div>
      <p>This note has not been prepared for entity extraction yet.</p>
      <div className="processing-actions"><button className="text-button" disabled={preparing} onClick={() => void prepare()}>{preparing ? "Preparing…" : "Prepare note"}</button></div>
      {error && <p className="library-error" role="alert">{error}</p>}
    </section>}
  </>;
}
