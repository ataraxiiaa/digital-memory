"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthGate } from "../components/auth-gate";
import { AccountMenu } from "../components/account-menu";
import { useAuth } from "../components/auth-provider";
import { DocumentLibrary } from "../components/document-library";

import { EntityLibrary } from "../components/entity-library";
import { TimelineView } from "../components/timeline-view";
import { SearchView } from "../components/search-view";

const views = ["Overview", "Documents", "Search", "Entities", "Timeline"] as const;
const viewIcons = { Overview: "O", Documents: "F", Search: "S", Entities: "E", Timeline: "D" } as const;

export default function Home() {
  return <AuthGate><Workspace /></AuthGate>;
}

function Workspace() {
  const [view, setView] = useState<(typeof views)[number]>("Overview");
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);
  const { user } = useAuth();
  return <div className="app-shell">
    <aside className="sidebar">
      <Link className="brand" href="/" aria-label="Digital Memory home"><span className="brand-mark">dm</span><span>digital memory<span className="brand-caption">A little more connected.</span></span></Link>
      <div className="workspace"><span className="avatar">{(user?.email?.[0] ?? "U").toUpperCase()}</span><div>Your workspace<small>Personal space</small></div><span className="workspace-dot" /></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{views.map((item, i) => <button key={item} className={view === item ? "nav-item active" : "nav-item"} onClick={() => setView(item)} aria-current={view === item ? "page" : undefined}><span aria-hidden="true">{viewIcons[item]}</span>{item}</button>)}</nav>
      <div className="sidebar-footer"><span className="demo-dot" />Your own collection<p>A place for the documents you choose to add.</p></div>
    </aside>
    <div className="main-shell"><header className="topbar"><span>Workspace <span className="slash">/</span><strong>{view}</strong></span><AccountMenu /></header>
      <main><div className="page-heading"><div><p className="eyebrow">YOUR PERSONAL KNOWLEDGE SPACE</p><h1>{view === "Overview" ? "Make room for remembering." : view === "Documents" ? "Your library, in one place." : view === "Search" ? "Find the exact words." : view === "Entities" ? "The people and things you remember." : "One moment leads to another."}</h1><p className="subtitle">A fresh space for your notes, ideas, and moments.</p></div></div>
        {view === "Search" ? <SearchView onOpenDocument={id => { setOpenDocumentId(id); setView("Documents"); }} /> : view === "Entities" ? <EntityLibrary /> : view === "Timeline" ? <TimelineView /> : <DocumentLibrary openDocumentId={openDocumentId} onOpened={() => setOpenDocumentId(null)} />}
        <footer className="main-footer"><span>Every memory starts with a source.</span><span>Your workspace</span></footer>
      </main>
    </div>
  </div>;
}
