"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth-provider";

export function AuthScreen({ mode }: { mode: "sign-in" | "sign-up" }) {
  const signup = mode === "sign-up";
  const { user, session, loading, error: sessionError, configured, signIn, signUp, signOut, retry } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    // Confirmation is handled by Supabase; users sign in after confirming.
    // Do not retain any unused implicit-flow tokens in the address bar.
    if (/access_token|refresh_token/.test(window.location.hash)) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, []);
  useEffect(() => { if (user && !loading) router.replace("/"); }, [user, loading, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    if (signup && password !== confirmation) { setError("Passwords don’t match."); return; }
    setSubmitting(true);
    setError(null);
    if (signup) {
      const result = await signUp(email, password);
      setError(result.error);
      if (!result.error) { setSent(result.needsConfirmation); setPassword(""); setConfirmation(""); }
    } else {
      setError(await signIn(email, password));
      setPassword("");
    }
    setSubmitting(false);
  }

  return <div className="sign-in-shell"><section className="sign-in-story"><Link className="brand" href="/"><span className="brand-mark">dm</span><span>digital memory</span></Link><div><p className="eyebrow">KEEP THE CONTEXT</p><h1>A place for what<br />you want to remember.</h1><p>Your notes, ideas, and moments.<br />Connected to the sources behind them.</p><div className="sign-in-art" aria-hidden="true"><span>notes</span><i /><span className="art-center">a little more<br />connected</span><i /><span>moments</span></div></div><small>Thoughtfully collected. Easily revisited.</small></section>
    <main className="sign-in-main"><div className="sign-in-card"><span className="sample-badge">Digital Memory</span><h1>{signup ? "Start your collection." : "Welcome back."}</h1><p className="subtitle">{signup ? "Create an account and make a space of your own." : "Sign in to your workspace."}</p>
      {!configured ? <div className="auth-alert" role="alert">Authentication isn’t configured yet. Please contact the workspace owner.</div>
        : loading || user ? <p className="auth-progress" role="status">Checking your session…</p>
        : session && sessionError ? <div className="auth-alert" role="alert"><p>{sessionError}</p><div className="auth-actions"><button className="button" onClick={retry}>Try again</button><button className="text-button" onClick={async () => setError(await signOut())}>Sign out</button></div>{error && <p>{error}</p>}</div>
        : sent ? <div className="signup-success" role="status"><h2>Check your email.</h2><p>If signup is available for this address, you’ll receive an email at <strong>{email}</strong>. Follow the confirmation link, then return here to sign in.</p><Link className="button" href="/sign-in">Back to sign in</Link></div>
        : <form onSubmit={submit}>
          <label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" placeholder="you@example.com" required value={email} onChange={event => setEmail(event.target.value)} disabled={submitting} />
          <label htmlFor="password">Password</label><div className="password-field"><input id="password" type={visible ? "text" : "password"} autoComplete={signup ? "new-password" : "current-password"} required minLength={signup ? 8 : undefined} aria-describedby={signup ? "password-help" : undefined} value={password} onChange={event => setPassword(event.target.value)} disabled={submitting} /><button type="button" aria-label={visible ? "Hide password" : "Show password"} aria-pressed={visible} onClick={() => setVisible(value => !value)}>{visible ? "Hide" : "Show"}</button></div>
          {signup && <><p id="password-help" className="sign-in-help">Use at least 8 characters. Longer, unique passwords are better.</p><label htmlFor="confirm-password">Confirm password</label><input id="confirm-password" type={visible ? "text" : "password"} autoComplete="new-password" required value={confirmation} onChange={event => setConfirmation(event.target.value)} disabled={submitting} /></>}
          {error && <p className="auth-alert" role="alert">{error}</p>}<button className="button sign-in-submit" type="submit" disabled={submitting}>{submitting ? signup ? "Creating account…" : "Signing in…" : signup ? "Create account" : "Sign in"}<span aria-hidden="true">→</span></button>
          <p className="sign-in-help">{signup ? "Already have an account? " : "New to Digital Memory? "}<Link className="text-button" href={signup ? "/sign-in" : "/sign-up"}>{signup ? "Sign in" : "Create an account"}</Link></p>
        </form>}
      <div className="sign-in-footnote"><span className="demo-dot" /><p>Your own space for the documents you choose to add.</p></div>
    </div></main>
  </div>;
}
