# Digital Memory frontend

Next.js App Router with Supabase sign-in/signup, private PDF upload, and an authenticated document library.

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

The same-origin proxy avoids browser CORS configuration and sends no passwords or refresh tokens to FastAPI. Identity responses are not cached. Frontend gating controls navigation; authorization for private data remains enforced in FastAPI and database RLS. The workspace includes no bundled documents and loads the user's document API.

## PDF upload and document library

After signing in, Overview and Documents show a PDF picker and the user's live document list. Choose a PDF up to **30 MiB (31,457,280 bytes)**, edit its title if desired, optionally enter a collection, then select **Upload PDF**. The backend must be running with the PDF upload migration applied and the private Supabase `documents` bucket configured.

Browser validation rejects wrong file types, empty files, and oversized PDFs before upload. FastAPI performs authoritative PDF validation. Progress measures transfer from the browser to Next.js; at 100% the UI switches to saving/validation while FastAPI and Supabase finish. Success clears the form and refreshes the first list page. Failures preserve the selection. After an uncertain upload outcome, refresh the list before retrying to avoid duplicates.

The library fetches owned metadata in pages of 20, with loading, empty, error/retry, and previous/next states. Select a document for metadata or existing text in an accessible dialog. Uploaded PDFs show **Uploaded · Text pending**. Original PDF viewing is implemented; extraction/OCR is not. Timeline is a placeholder for future extracted events.

Same-origin `/api/documents`, `/api/documents/upload`, and `/api/documents/[id]` routes forward the user's token to FastAPI without an admin key. The upload proxy streams multipart bytes, limits total bytes to 30 MiB plus 64 KiB of overhead, and waits up to 120 seconds. It never automatically retries a create. Production hosting must support these request sizes and durations; smaller platform caps can prevent large files reaching the app.

UI code lives in `components/document-library.tsx`, proxy code in `lib/document-proxy.ts`, and styles in `app/documents.css`. Session changes unmount the protected workspace and abort in-flight UI requests. Document data is not persisted in browser storage.

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

Tests under `tests/frontend/e2e/` cover authentication, signup, PDF selection/upload, list refresh, validation, failures, pagination, dialogs, and mobile layout. They use mock Supabase sessions and responses. Proxy tests send real multipart requests through Next.js to a local test upstream, verifying bytes and token propagation. No real accounts or Supabase files are created. Tests use port 3100 with isolated `.next-e2e/` output and a local upstream on port 3199. Screenshots are saved under ignored `test-results/`.

## Private PDF viewing

Select an uploaded PDF to open its details and embedded browser PDF viewer. FastAPI's authenticated `GET /documents/{id}/file` checks the document row and expected owner-specific file path, then downloads the original using the same user's token. Next.js streams those bytes through `/api/documents/[id]/file` without publishing a Storage URL. Responses are no-store.

The browser fetches with the bearer token, creates a temporary local blob URL, and uses that URL in the PDF frame. Tokens are never placed in URLs. The URL is revoked when the viewer closes, changes document, or unmounts on sign-out. Open-in-new-tab and download links provide fallbacks for browsers without embedded PDF support. A downloaded or already-opened copy cannot be revoked by signing out.

The original PDF is fetched in full (up to 30 MiB) before viewing. Native viewer controls provide pagination and zoom where supported. This is a browser-native viewer, not a custom PDF.js renderer. Headless browser tests verify authenticated fetching, binary forwarding, frame setup, fallback links, retry, and URL cleanup; they do not guarantee native PDF rendering on every browser. No page text is extracted or stored by this feature.
