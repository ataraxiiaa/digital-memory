"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../components/auth-provider";

export default function SignIn() {
  const { user, session, loading, error: sessionError, configured, signIn, signOut, retry } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => { if (user && !loading) router.replace("/"); }, [user, loading, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    const result = await signIn(email, password);
    setPassword("");
    setError(result);
    setSubmitting(false);
  }

  return <div className="sign-in-shell"><section className="sign-in-story"><a className="brand" href="/"><span className="brand-mark">dm</span><span>digital memory</span></a><div><p className="eyebrow">KEEP THE CONTEXT</p><h1>A place for what<br />you want to remember.</h1><p>Your notes, ideas, and moments.<br />Connected to the sources behind them.</p><div className="sign-in-art" aria-hidden="true"><span>notes</span><i /><span className="art-center">a little more<br />connected</span><i /><span>moments</span></div></div><small>Thoughtfully collected. Easily revisited.</small></section><main className="sign-in-main"><div className="sign-in-card"><span className="sample-badge">Digital Memory</span><h1>Welcome back.</h1><p className="subtitle">Sign in to your workspace.</p>
    {!configured ? <div className="auth-alert" role="alert">Sign-in isn’t configured yet. Please contact the workspace owner.</div> : loading || user ? <p className="auth-progress" role="status">Checking your session…</p> : session && sessionError ? <div className="auth-alert" role="alert"><p>{sessionError}</p><div className="auth-actions"><button className="button" onClick={retry}>Try again</button><button className="text-button" onClick={async () => setError(await signOut())}>Sign out</button></div>{error && <p>{error}</p>}</div> : <form onSubmit={submit}>
      <label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" placeholder="you@example.com" required value={email} onChange={event => setEmail(event.target.value)} disabled={submitting} />
      <label htmlFor="password">Password</label><div className="password-field"><input id="password" type={visible ? "text" : "password"} autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} disabled={submitting} /><button type="button" aria-label={visible ? "Hide password" : "Show password"} aria-pressed={visible} onClick={() => setVisible(value => !value)}>{visible ? "Hide" : "Show"}</button></div>
      {error && <p className="auth-alert" role="alert">{error}</p>}<button className="button sign-in-submit" type="submit" disabled={submitting}>{submitting ? "Signing in…" : "Sign in"}<span aria-hidden="true">→</span></button><p className="sign-in-help">Use an existing account. Need access or help with your password? Contact the workspace owner.</p>
    </form>}
    <div className="sign-in-footnote"><span className="demo-dot" /><p>Your own space for the documents you choose to add.</p></div></div></main></div>;
}
