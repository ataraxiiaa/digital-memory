"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth-provider";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, session, error, signOut, retry } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !session) router.replace("/sign-in");
  }, [loading, session, router]);
  if (error) return <div className="auth-status"><div role="alert"><h1>Let’s reconnect.</h1><p>{error}</p><div className="auth-actions"><button className="button" onClick={retry}>Try again</button><button className="text-button" onClick={() => void signOut()}>Sign out</button></div></div></div>;
  if (loading || !user) return <div className="auth-status" role="status">Checking your session…</div>;
  return children;
}
