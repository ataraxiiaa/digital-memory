"use client";

import { useRef, useState, type FormEvent } from "react";
import { useAuth } from "./auth-provider";

export const MAX_NOTE_CHARS = 200_000;

export function NewNote({ onCreated }: { onCreated: () => void }) {
  const { session, signOut } = useAuth();
  const token = session?.access_token;
  const [title, setTitle] = useState("");
  const [collection, setCollection] = useState("");
  const [noteDate, setNoteDate] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const submitting = useRef(false);

  const tooLong = content.length > MAX_NOTE_CHARS;
  const ready = title.trim() !== "" && content.trim() !== "" && !tooLong;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !ready || submitting.current) return;
    submitting.current = true; setSaving(true); setError(null); setSuccess(null);
    const authorization = { Authorization: `Bearer ${token}` };
    try {
      // The note text is sent exactly as typed; the server never trims or rewrites it.
      const created = await fetch("/api/documents", {
        method: "POST", headers: { ...authorization, "Content-Type": "application/json" }, cache: "no-store",
        body: JSON.stringify({ title: title.trim(), collection: collection.trim() || "Notes", document_date: noteDate || null, content }),
      });
      if (created.status === 401) { await signOut(); return; }
      const note = await created.json().catch(() => null);
      if (created.status !== 201 || !note?.id) throw new Error(typeof note?.detail === "string" ? note.detail : "Could not save the note.");
      setTitle(""); setCollection(""); setNoteDate(""); setContent("");
      onCreated();
      const prepared = await fetch(`/api/documents/${note.id}/process-text`, { method: "POST", headers: authorization, cache: "no-store" });
      if (prepared.status === 401) { await signOut(); return; }
      setSuccess(prepared.ok
        ? `“${note.title}” was saved. Open it to find people, dates, events and places.`
        : `“${note.title}” was saved, but it could not be prepared yet. Open it and choose “Prepare note” to retry.`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save the note.");
    } finally { submitting.current = false; setSaving(false); }
  }

  return <section className="upload-panel note-panel" aria-labelledby="note-heading">
    <div><p className="eyebrow">WRITE OR PASTE</p><h2 id="note-heading">Add a note.</h2><p>Plain text stays exactly as you write it, so every suggestion can point back to the precise words.</p><small>Up to {MAX_NOTE_CHARS.toLocaleString("en")} characters</small></div>
    <form onSubmit={event => void submit(event)} aria-label="New note">
      <div className="upload-fields">
        <label>Title<input required maxLength={300} value={title} disabled={saving} onChange={event => setTitle(event.target.value)} /></label>
        <label>Collection for this note (optional)<input maxLength={100} placeholder="Notes" value={collection} disabled={saving} onChange={event => setCollection(event.target.value)} /></label>
        <label>Date of the note (optional)<input type="date" value={noteDate} disabled={saving} onChange={event => setNoteDate(event.target.value)} /></label>
        <label>Note<textarea required rows={7} value={content} disabled={saving} onChange={event => setContent(event.target.value)} /></label>
      </div>
      <p className="note-count" data-over={tooLong}>{content.length.toLocaleString("en")} / {MAX_NOTE_CHARS.toLocaleString("en")} characters</p>
      <button className="button" type="submit" disabled={!ready || saving}>{saving ? "Saving note…" : "Save note"}</button>
      {error && <p className="library-error" role="alert">{error}</p>}{success && <p className="library-success" role="status">{success}</p>}
    </form>
  </section>;
}
