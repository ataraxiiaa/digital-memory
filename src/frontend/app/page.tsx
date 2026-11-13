"use client";

import { useState } from "react";
import { filterDocuments, formatDate, type MemoryDocument } from "../lib/sample-data";

const views = ["Overview", "Documents", "Timeline"] as const;
type View = (typeof views)[number];

export default function Home() {
  const [view, setView] = useState<View>("Overview");
  const [query, setQuery] = useState("");
  const [collection, setCollection] = useState("All collections");
  const [selected, setSelected] = useState<MemoryDocument | null>(null);
  const [saved, setSaved] = useState<string[]>([]);
  const filtered = filterDocuments(query, collection);

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Digital Memory home"><span className="brand-mark">dm</span><span>digital memory<span className="brand-caption">A little more connected.</span></span></a>
      <div className="workspace"><span className="avatar">A</span><div>Alex’s workspace<small>Sample workspace</small></div><span className="workspace-dot" /></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{views.map((item, i) => <button key={item} className={view === item ? "nav-item active" : "nav-item"} onClick={() => setView(item)}><span aria-hidden="true">{["▦", "▤", "◷"][i]}</span>{item}{item === "Documents" && <span className="count">6</span>}</button>)}</nav>
      <div className="nav-label collection-heading">COLLECTIONS</div>
      <div className="collection-nav">{["Personal", "Studio", "Learning"].map(name => <button key={name} onClick={() => { setCollection(name); setView("Documents"); }}><span className={`collection-dot ${name.toLowerCase()}`} />{name}<span className="count">2</span></button>)}</div>
      <div className="sidebar-footer"><span className="demo-dot" />Sample data only<p>A space to explore. All names and documents here are fictional.</p></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><span>Workspace <span className="slash">/</span> <strong>{view}</strong></span><span className="sample-badge">Sample workspace</span></header>
      <main>
        <div className="page-heading"><div><p className="eyebrow">YOUR PERSONAL KNOWLEDGE SPACE</p><h1>{view === "Overview" ? "Make room for remembering." : view === "Documents" ? "Your library, in one place." : "One moment leads to another."}</h1><p className="subtitle">{view === "Overview" ? "Find the notes, ideas, and moments that belong together." : view === "Documents" ? "Explore the original notes behind every connection." : "Follow the story across your notes and conversations."}</p></div><span className="heading-symbol" aria-hidden="true">✳</span></div>

        {view === "Overview" && <>
          <div className="stats"><div><span>Documents</span><strong>06<small>across 3 collections</small></strong></div><div><span>People mentioned</span><strong>02<small>in the sample notes</small></strong></div><div><span>Timeline moments</span><strong>06<small>November 2026</small></strong></div></div>
          <section className="feature"><div><span className="eyebrow">FOLLOW A THREAD</span><h2>Small ideas. Shared context.</h2><p>Two studio conversations share a simple idea: keep the source close to the finding.</p><button className="text-button" onClick={() => {setCollection("Studio"); setView("Documents");}}>Explore the studio notes <span>↗</span></button></div><div className="thread" aria-label="Sample connection: Alex Morgan and Sam Chen are mentioned in two studio notes"><span>Alex Morgan</span><div className="thread-line" /><span className="thread-center">Studio project<small>2 source notes</small></span><div className="thread-line" /><span>Sam Chen</span></div></section>
        </>}

        <section className="library" aria-label={view === "Timeline" ? "Sample timeline" : "Sample documents"}>
          <div className="section-heading"><div><h2>{view === "Overview" ? "Recent memories" : view === "Documents" ? "All documents" : "November 2026"}</h2><p>{view === "Timeline" ? "A chronological view of the sample collection." : "Original sources, ready to revisit."}</p></div>{view === "Overview" && <button className="text-button" onClick={() => setView("Documents")}>View all documents <span>→</span></button>}</div>
          <div className="filters"><label className="search"><span aria-hidden="true">⌕</span><input aria-label="Search sample documents" placeholder="Search notes, people, or ideas…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button aria-label="Clear search" onClick={() => setQuery("")}>×</button>}</label><select aria-label="Filter by collection" value={collection} onChange={event => setCollection(event.target.value)}>{["All collections", "Personal", "Studio", "Learning"].map(name => <option key={name}>{name}</option>)}</select></div>
          <div className="result-caption" role="status">{filtered.length} sample {filtered.length === 1 ? "document" : "documents"}{query && ` matching “${query}”`}</div>
          {filtered.length === 0 ? <div className="empty"><h3>No matching memories</h3><p>Try another word or explore all collections.</p><button className="button" onClick={() => {setQuery(""); setCollection("All collections");}}>Clear filters</button></div> : <div className={view === "Timeline" ? "timeline" : "document-list"}>{filtered.map(doc => <button className="document-row" key={doc.id} onClick={() => setSelected(doc)}>{view === "Timeline" ? <time dateTime={doc.date}>{formatDate(doc.date)}</time> : <span className="document-icon" aria-hidden="true">{doc.type === "Meeting" ? "☷" : "≡"}</span>}<span className="document-info"><span className="document-title">{doc.title}{saved.includes(doc.id) && <span className="saved-label">Saved</span>}</span><span className="document-excerpt">{doc.excerpt}</span><span className="document-meta">{doc.type}<span>·</span>{view !== "Timeline" && <>{formatDate(doc.date)}<span>·</span></>}{doc.tags[0]}</span></span><span className="collection-pill">{doc.collection}</span><span className="row-arrow" aria-hidden="true">↗</span></button>)}</div>}
        </section>
        <footer className="main-footer"><span>Every memory starts with a source.</span><span>Frontend preview · Synthetic sample data</span></footer>
      </main>
    </div>

    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><dialog open className="detail" aria-labelledby="detail-title" onCancel={event => {event.preventDefault(); setSelected(null);}} ref={element => element?.focus()} tabIndex={-1} onKeyDown={event => {if (event.key === "Tab") { const controls = event.currentTarget.querySelectorAll<HTMLButtonElement>("button"); const first = controls[0]; const last = controls[controls.length - 1]; if(event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)){event.preventDefault(); last.focus();} else if(!event.shiftKey && document.activeElement === last){event.preventDefault();first.focus();}} if(event.key === "Escape") setSelected(null);}} onClick={event => event.stopPropagation()}><div className="detail-top"><span className="sample-badge">Synthetic sample document</span><button className="close" aria-label="Close document" onClick={() => setSelected(null)}>×</button></div><p className="eyebrow">{selected.collection} / {selected.type}</p><h2 id="detail-title">{selected.title}</h2><p className="subtitle">{formatDate(selected.date)}</p><h3>Original passage</h3><blockquote>{selected.excerpt}</blockquote><h3>People mentioned</h3><p>{selected.people.join(", ") || "No people mentioned."}</p><div className="tags">{selected.tags.map(tag => <span key={tag}>{tag}</span>)}</div><button className="button" onClick={() => setSaved(previous => previous.includes(selected.id) ? previous.filter(id => id !== selected.id) : [...previous, selected.id])}>{saved.includes(selected.id) ? "Remove from saved" : "Save for this session"}</button><p className="detail-note">This fictional note is for exploring the interface. Saved items reset when the page reloads.</p></dialog></div>}
  </div>;
}
