# Digital Memory frontend

Next.js App Router with Supabase email/password sign-in, signup, and an empty personal workspace.

## Configure

Create `src/frontend/.env.local` using `.env.example` and your project's **publishable** URL/key. These public values are bundled into browser JavaScript. Never copy a database password, secret key, or service-role key into frontend configuration.

`BACKEND_API_URL` is server-only; it defaults to `http://127.0.0.1:8000`. It must point to the FastAPI service, use HTTPS when deployed, and be reachable from the Next.js server. Restart the frontend after changing environment variables; rebuild deployed apps after changing `NEXT_PUBLIC_` values.

## Run

From the workspace root, start FastAPI:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd src/frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:3000/sign-in to sign in or http://localhost:3000/sign-up to create an account. Signup checks matching passwords and requires at least 8 characters; Supabase enforces any additional project password requirements.

Enable new-user signup and email/password authentication in Supabase. In Auth URL configuration, set your Site URL and allow the exact frontend `/sign-in` redirect URL (locally `http://localhost:3000/sign-in`; add `http://127.0.0.1:3000/sign-in` if used). For deployment, add the actual HTTPS frontend URL. Signup passes the current origin's `/sign-in` URL as `emailRedirectTo`.

If email confirmation is enabled, signup shows a check-email message without opening the workspace. Follow the Supabase confirmation link, then sign in manually. This app does not consume URL-fragment sessions; unused implicit-flow token fragments are removed from the address bar. If confirmation is disabled and Supabase returns a session, the existing backend identity check runs before opening the empty workspace. Responses avoid claiming an email address is available, because Supabase can obscure existing-account signups. Password recovery, OAuth, and MFA challenge screens are not implemented.

## Sign-in flow

1. The form calls Supabase `signInWithPassword`; passwords go directly to Supabase.
2. The browser SDK persists the session in local storage and handles token refresh.
3. The auth provider sends the access token to `/api/auth/me`, a Next.js server route.
4. That route forwards it to FastAPI `/auth/me`. FastAPI verifies the token with Supabase.
5. Only after verification does the UI display the empty personal workspace. A reload restores and rechecks the session. Backend failures show retry/sign-out controls; a rejected session is cleared locally.
6. Sign out clears this browser's Supabase session. It does not sign out other devices. Previously issued access tokens can remain valid until expiration.

The same-origin proxy avoids browser CORS configuration and sends no passwords or refresh tokens to FastAPI. Identity responses are not cached. Frontend gating controls navigation; authorization for actual private data must remain enforced in FastAPI and database RLS. The workspace includes no bundled documents. Original document storage APIs are still upcoming.

Code lives in `components/auth-provider.tsx`, `components/auth-screen.tsx`, `components/auth-gate.tsx`, `app/sign-in/page.tsx`, `app/sign-up/page.tsx`, `lib/supabase.ts`, and `app/api/auth/me/route.ts`. `components/account-menu.tsx` displays the real signed-in account.

## Verify

From `src/frontend/`:

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
npx.cmd playwright install chromium --only-shell
npm.cmd run test:e2e
```

Browser tests under `tests/frontend/e2e/` use mock Supabase sessions and API responses. They cover redirects, sign-in errors, verification, reload persistence, sign-out, expired/rejected sessions, outage recovery, and mobile layout. They do not create real accounts or prove a successful live sign-in. Tests run a separate development server on port 3100 using `.next-e2e/` and fake public configuration so the existing dev server can keep running. Screenshots are written to ignored `test-results/`.
