"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { getSupabaseClient } from "../lib/supabase";

type User = { id: string; email: string | null; role: "authenticated" };
type AuthContextValue = {
  session: Session | null;
  user: User | null;
  loading: boolean;
  error: string | null;
  configured: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signOut: () => Promise<string | null>;
  retry: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [configured, setConfigured] = useState(true);
  const [verified, setVerified] = useState<{ token: string; user: User } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const client = getSupabaseClient();
    if (!client) {
      setConfigured(false);
      setInitialized(true);
      return;
    }
    // Keep this callback synchronous; verify with FastAPI in a separate effect.
    const { data: { subscription } } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setInitialized(true);
      setError(null);
    });
    return () => subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!session) { setVerified(null); setChecking(false); return; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let active = true;
    setChecking(true);
    setError(null);
    async function verify() {
      try {
        const response = await fetch("/api/auth/me", {
          headers: { Authorization: `Bearer ${session!.access_token}` },
          cache: "no-store", signal: controller.signal,
        });
        if (!active) return;
        if (response.status === 401) {
          await getSupabaseClient()?.auth.signOut({ scope: "local" });
          if (active) { setSession(null); setVerified(null); }
          return;
        }
        if (!response.ok) throw new Error("unavailable");
        const user = await response.json();
        if (typeof user.id !== "string" || user.role !== "authenticated") throw new Error("invalid");
        if (active) setVerified({ token: session!.access_token, user });
      } catch {
        if (active) { setVerified(null); setError("We couldn’t verify your session. Check that the API is running, then try again."); }
      } finally {
        clearTimeout(timeout);
        if (active) setChecking(false);
      }
    }
    void verify();
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [session, attempt]);

  const signIn = useCallback(async (email: string, password: string) => {
    const client = getSupabaseClient();
    if (!client) return "Sign-in isn’t configured. Please contact the workspace owner.";
    try {
      const { error } = await client.auth.signInWithPassword({ email: email.trim(), password });
      if (!error) return null;
      if (error.code === "email_not_confirmed") return "Confirm your email before signing in.";
      if (error.status === 429) return "Too many attempts. Please wait a little before trying again.";
      return "Couldn’t sign in. Check your email and password and try again.";
    } catch {
      return "Couldn’t reach sign-in. Check your connection and try again.";
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      const { error } = await getSupabaseClient()!.auth.signOut({ scope: "local" });
      if (error) return "Couldn’t sign out. Please try again.";
      setSession(null);
      setVerified(null);
      setError(null);
      return null;
    } catch { return "Couldn’t sign out. Please try again."; }
  }, []);

  const user = session && verified?.token === session.access_token ? verified.user : null;
  const loading = !initialized || (!!session && !error && (!user || checking));
  return <AuthContext.Provider value={{ session, user, loading, error, configured, signIn, signOut, retry: () => setAttempt(value => value + 1) }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth requires AuthProvider");
  return value;
}
