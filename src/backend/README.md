# Backend checkpoints

We add one working layer at a time. Each checkpoint ends with a runnable result and checks before the next layer.

1. **API foundation (implemented):** FastAPI application, `GET /health`, API documentation, and request-level tests.
2. **Supabase document storage:** the migration is applied, catalog checks passed, and authenticated create/list/read API routes are implemented. Live two-user isolation testing remains pending.
3. **Text ingestion:** allow users to add their own documents; handle long texts in passages, keeping original text and source positions. Define size limits and clear errors, preserve Unicode, and prevent silent truncation. No bundled document corpus remains.
4. **Search and evidence:** search passages, paginate results, and return exact source references. Verify matches from the start, middle, and end of long files.
5. **Frontend integration:** connect the existing library and source previews, add loading/error/empty states, and configure local browser access.

Entity extraction, inferred event timelines, and relationship graphs follow reliable document storage and evidence retrieval. Document dates and dates mentioned inside documents must stay distinct.

## Checkpoint 2a: Supabase storage boundary

We are using Supabase from the start instead of SQLite. The planned request path is:

```text
Next.js -> FastAPI (user access token) -> document store -> Supabase PostgreSQL
```

Pydantic input and response schemas live in `schemas/documents.py`; the health response schema lives in `schemas/health.py`. These define Python validation and response shapes. The database table structure remains in `supabase/migrations/202611130001_documents.sql`; there are no ORM models because we use the Supabase Data API.

The `DocumentStore` interface in `storage/documents.py` defines create, list, and get operations. `SupabaseDocumentStore` implements them using Supabase's Data API through HTTPX. Routes can call these operations without knowing the database request details. Listing returns metadata only, so opening a library does not download every full book.

PostgreSQL stores the title, text, document date, collection, synthetic-data flag, owner, and creation timestamp. Original uploaded files will go into a private Supabase Storage bucket in the ingestion checkpoint; there is no bucket or file-upload integration yet. Supabase is our managed data service; Next.js and FastAPI will require their own deployment hosts.

### Connect the project in the next checkpoint

1. Create or select the intended Supabase development project.
2. Review `supabase/migrations/202611130001_documents.sql`, then use the migration command below. It creates the table, index, grants, and ownership policies in one tracked transaction. The initial migration has already been applied to the configured project; the runner safely skips recorded migrations.
3. Use the project URL and **publishable** key from the project settings. Add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` to the workspace-root `.env`, preserving existing entries. Connection helpers load that file automatically; process environment variables take precedence. `.env.example` documents the names. Database passwords and secret keys are not needed for this Data API connection.
4. Enable the intended Supabase Auth sign-in method and create two development test users. The adapter requires a user's access token; a publishable key alone does not authorize document access.
5. Verify with user A: insert and retrieve a document. Verify with user B: A's document must not appear in a list or by ID. Requests without a user token must not read or create documents. Update/delete should be denied in this checkpoint.

Ownership comes from `auth.uid()`, not a client-provided owner ID. Row-level security lets each authenticated user read and insert only their own rows. The adapter uses a publishable key plus a user token and rejects new secret keys. Do not replace it with a secret/service-role key, which would bypass these policies.

The initial document migration has been applied to the configured Supabase project. Live database catalog checks verified all eight columns, the index, enabled RLS, both policy names, and the intended role permissions. Authenticated document routes now use the adapter. Offline tests do **not** prove two-user RLS isolation; that remains pending. Upload limits, passage splitting, and original-file storage are still upcoming.

### Apply migrations

After installing the updated requirements, run from the workspace root:

```powershell
.\.venv\Scripts\python.exe -m src.backend.supabase.migrate
```

This administrative command reads the project URL and `SUPABASE_DATABASE_PASSWORD` from the ignored root `.env` or process environment. It connects directly to PostgreSQL over encrypted TLS. Direct database access requires IPv6 connectivity or the project's IPv4 add-on; the Data API connection check alone does not establish database reachability.

The runner applies migration SQL and a checksum history entry in one transaction, with verification before commit. It uses `digital_memory_meta.schema_migrations`, a private application-owned history table separate from Supabase CLI migration history. A transaction lock prevents concurrent runs. Recorded files are skipped, changed checksums are rejected, and an untracked existing documents table requires review. Keep applied SQL files unchanged and add a new migration for future changes. Do not also apply these files manually or through the Supabase CLI without reconciling history first.

The migration creates the empty document table; it does not upload fixtures or create users. `/health` and the regular Data API adapter do not use the database password.

### Read-only connection check

After installing the updated requirements, run from the workspace root:

```powershell
.\.venv\Scripts\python.exe -m src.backend.check_supabase
```

This checks the Supabase Auth health endpoint with the configured publishable key. It prints no keys, passwords, tokens, or document content. A pass verifies service connectivity, not the document schema or user isolation. Missing configuration or failed requests exit with code 1.

After applying the migration and signing in a development user, run:

```powershell
.\.venv\Scripts\python.exe -m src.backend.check_supabase --documents
```

Paste that user's access token at the hidden terminal prompt; do not put it in a command argument or commit it. This verifies the user through Supabase Auth and reads at most one document's metadata. It makes no writes. Cross-user isolation still needs the separate two-user test described above.

`storage/connection.py` provides `open_document_store(access_token)` for standalone callers. HTTP document routes instead reuse the authentication dependency's request-scoped HTTP client and settings. `/health` remains independent of Supabase so the API can still start without cloud configuration. Connection helpers use direct HTTPS with a 15-second diagnostic timeout and do not use environment proxies.

References: [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys), [row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security), and [database and Storage architecture](https://supabase.com/docs/guides/getting-started/the-supabase-way-to-build).

## Backend authentication API

`GET /auth/me` is now implemented. It accepts `Authorization: Bearer <Supabase user access token>`, calls the configured project's `/auth/v1/user` endpoint, and returns only the verified user's `id`, `email`, `role`, and `email_confirmed_at`. No token decoding without verification is used. Supabase handles sign-in, passwords, session issuance, and refresh; this API does not implement a second password/login system.

Example response:

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "email": "alex@example.test",
  "role": "authenticated",
  "email_confirmed_at": "2026-11-13T10:00:00Z"
}
```

To try it, start the backend, open http://127.0.0.1:8000/docs, select **Authorize**, and enter a signed-in Supabase user's **access token** (without the `Bearer` prefix). Then execute `GET /auth/me`. A project publishable key, refresh token, database password, or administrative key is not a user access token. Tokens expire; sign in or refresh through Supabase when needed. The endpoint does not impose an additional email-confirmation policy; that behavior follows the configured Supabase sign-in settings.

| Result | HTTP status |
| --- | --- |
| Verified authenticated user | 200 |
| Missing/malformed header or Supabase-rejected token | 401 with a Bearer challenge |
| Missing configuration, timeout, provider rate limit, or provider failure | 503 |
| Malformed user identity returned by the provider | 502 |

Responses use `Cache-Control: no-store`. Errors omit credentials and upstream response bodies. Each request is validated remotely, so protected endpoints depend on Supabase Auth availability. `/health` and API docs stay public. The reusable `get_authenticated_session` dependency retains the verified user and a masked access token used by document routes; tokens must not be logged.

Code is separated into `auth/dependencies.py` (token checks and validation), `auth/routes.py` (endpoint), and `schemas/auth.py` (response schema). Tests in `tests/backend/test_auth.py` mock Supabase for valid, expired/rejected, malformed, unavailable, and timeout scenarios. A successful mock test is not a live sign-in or a two-user RLS test. Frontend signup and sign-in are implemented; document UI integration remains upcoming.

## Document API

### PDF uploads

`POST /documents/upload` now accepts an authenticated `multipart/form-data` request with:

- `file` (required): PDF, up to **31,457,280 bytes (30 MiB)**, matching the current private `documents` bucket.
- `title` (optional): defaults to the PDF filename without the extension.
- `collection` (optional): defaults to `Unfiled`.

Open `/docs`, authorize with a Supabase user's access token, expand this endpoint, and choose a PDF in **Try it out**. Do not manually set the multipart Content-Type; the client must generate its boundary.

The endpoint checks the extension and supplied MIME type, reads the file with a byte limit, checks its PDF header, and opens its page tree with `pypdf`. Empty, damaged, encrypted/password-protected, and over-1,000-page files are rejected. Accepted MIME types are `application/pdf` and `application/octet-stream` for clients that do not identify PDFs correctly. Validating a PDF is not malware scanning or complete page rendering; parsing currently runs in a thread without hard CPU/memory isolation.

The original bytes are stored without overwrite under `documents/<user-id>/<document-id>/original.pdf`. Filenames are metadata, not storage paths. The same user's token authorizes both Storage and database requests; no secret/service-role key is used. A document row stores the original filename, byte count, page count, and private storage path. A successful response is 201 with the document and a `Location` header.

Uploaded PDFs have `status: "uploaded"` and empty `content`. This explicitly means **stored, not yet text-extracted**. Existing JSON text creates have `status: "ready"`. Scanned PDFs are accepted for storage, but OCR detection/extraction and page-level evidence have not been implemented. Uploading does not run a background worker or claim that the PDF is searchable.

Migration `202611130002_pdf_uploads.sql` has been applied to the configured project. It adds PDF metadata and permits authenticated users to insert/read only their own Storage folder. Cleanup may delete unlinked objects in that folder; linked originals are protected from this cleanup policy. No update/overwrite policy is added. This policy assumes no other permissive Storage policies grant broader access; the live bucket had no policies before this migration.

Storage and Postgres are separate services, so saving both is not a single atomic transaction. If metadata saving fails or times out, the endpoint attempts to remove the uploaded object. A policy refuses removal when a document row already committed, preserving the original after an ambiguous timeout. Cleanup failure emits a sanitized operational warning; administrative reconciliation may be needed. On a 503 after upload, refresh the document list before retrying, because a create is not idempotent.

Errors: 401 for missing/invalid authentication, 413 for too many file bytes, 415 for unsupported file type, 422 for invalid PDF or form fields, and 503 for storage/database failures. The application byte limit applies when reading the parsed uploaded file; also configure a multipart request-size limit slightly above 30 MiB at the deployment ingress, because multipart parsing/spooling happens before the route runs. The frontend now includes upload and document-list controls through Next.js proxies. The authenticated original-file endpoint `GET /documents/{document_id}/file` is now implemented for viewing/download.

Tests in `tests/backend/test_pdf_uploads.py` validate in-memory PDFs and mock cloud upload/metadata/cleanup requests. No real documents or accounts are created by the tests. Live user-token upload and two-user file access checks remain pending.

All operations require `Authorization: Bearer <user access token>`. Open `/docs`, authorize with a signed-in user's access token, and use the Documents section.

| Method and path | Result |
| --- | --- |
| `POST /documents` | Save text; returns full document with status 201 and a `Location` header. |
| `GET /documents?limit=20&offset=0` | Return a JSON array of owned document metadata, ordered newest first with ID as a tie breaker. |
| `GET /documents/{document_id}` | Return an owned document including its original text. |

Example create body:

```json
{
  "title": "My first note",
  "content": "Original text.\n\nAnother passage.",
  "document_date": "2026-11-13",
  "collection": "Personal",
  "synthetic": false
}
```

Only `title` and `content` are required for JSON text creation. Defaults are a null document date, `Unfiled` collection, and `synthetic: false`. Do not send `owner_id`, `id`, or timestamps: additional fields are rejected. Supabase derives ownership from the same verified access token forwarded by FastAPI. Text and paragraph breaks are preserved. The JSON endpoints do not import files; PDF upload is a separate multipart endpoint above. Passage splitting, search, update, and document deletion are not implemented. The JSON text-create endpoint has no content-size cap yet.

Lists omit full content and owner IDs, return `[]` when empty, and allow `limit` 1–100 and nonnegative `offset`. Invalid bodies, pagination, or UUIDs return 422. Missing/invalid authentication returns 401; a nonexistent or RLS-hidden document returns the same 404. Storage failures return a sanitized 503. Document responses, including validation errors, use `Cache-Control: no-store`. Avoid automatic retries of creates: duplicate submission currently creates separate documents.

`routes/documents.py` contains routes and storage wiring. Tests in `tests/backend/test_document_routes.py` cover the HTTP contracts, token forwarding, input validation, pagination, missing/hidden rows, error handling, and lack of token sharing between requests. Live end-to-end tests with two real accounts remain pending; no users or documents are automatically seeded by these tests.

## Health request flow

```text
Browser or test client -> GET /health -> FastAPI route -> JSON response
```

`main.py` creates the application. The route is a Python function that handles a particular HTTP method and path. `HealthResponse` defines the response shape. Uvicorn runs the app and accepts HTTP requests; FastAPI routes those requests to the function.

The response is `{"status":"ok","service":"digital-memory-api"}`. It proves that the application responds, not that a database or ingestion pipeline is ready. This checkpoint performs no document imports and makes no changes to the frontend.

## Install and run

From the workspace root in PowerShell, using the existing Python environment and installed `uv` package manager:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r src/backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

The development server runs until Ctrl+C. Open:

- http://127.0.0.1:8000/health — check the JSON response.
- http://127.0.0.1:8000/docs — expand `GET /health`, select **Try it out**, then **Execute**.

Or run `Invoke-RestMethod http://127.0.0.1:8000/health` in another PowerShell terminal.

No credentials or configuration files are required. The server binds to the local machine. The application has no authentication yet.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`pytest.ini` adds `src` to the Python import path and selects `tests/backend`. Tests send requests in process; they do not need a running server. They cover health JSON, the schema and docs, unknown routes, and unsupported HTTP methods. No formatter or linter is configured yet.

`requirements.in` declares direct runtime and development dependencies. `requirements.txt` locks the resolved dependencies. To intentionally update the lockfile:

```powershell
uv pip compile src/backend/requirements.in --python .\.venv\Scripts\python.exe -o src/backend/requirements.txt
```

## Original PDF viewing endpoint

`GET /documents/{document_id}/file` requires the same bearer authentication as other document routes. It reads the owned document row before accessing private Storage and requires the canonical `<user-id>/<document-id>/original.pdf` path. Missing, hidden, text-only, or mismatched paths return 404 without fetching the object. Storage authentication expiry returns 401, inaccessible/missing objects return 404, and service failures return 503. Oversized or non-PDF stored bytes return 502.

The successful response returns original bytes as `application/pdf`, with inline Content-Disposition, UTF-8 filename encoding, no-store, and nosniff headers. No signed or public URL is issued. The backend buffers at most 30 MiB before responding; it does not implement range requests. The frontend fetches a full file through its server proxy before displaying a temporary browser-local URL. No migration or text extraction is needed for viewing. Tests mock Storage and verify byte preservation, ownership/path checks, content headers, authentication, and failure handling.
