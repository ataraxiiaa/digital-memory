"use client";

import { useState } from "react";
import { useAuth } from "./auth-provider";

export function AccountMenu() {
  const { user, signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return <div className="account-menu"><span title={user?.email ?? "Signed in"}>{user?.email ?? "Signed in"}</span><button className="text-button" disabled={busy} onClick={async () => { setBusy(true); setError(await signOut()); setBusy(false); }}>{busy ? "Signing out…" : "Sign out"}</button>{error && <span role="alert">{error}</span>}</div>;
}
