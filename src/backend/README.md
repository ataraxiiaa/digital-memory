# Backend checkpoints

We add one working layer at a time. Each checkpoint ends with a runnable result and checks before the next layer.

1. **API foundation (implemented):** FastAPI application, `GET /health`, API documentation, and request-level tests.
2. **Supabase document storage:** (2a) prepare the schema and storage adapter; (2b) connect the Supabase project and verify ownership with two test users; (2c) add authenticated create/list/read API routes. The schema and offline adapter are prepared; hosted verification and routes remain pending.
3. **Text ingestion:** import the ten synthetic notes; then handle the downloaded long texts in passages, keeping original text and source positions. Define size limits and clear errors, preserve Unicode, and prevent silent truncation.
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
2. Review `supabase/migrations/202611130001_documents.sql`, then run it once in that project's SQL editor. It creates the table, index, grants, and ownership policies in one transaction. It deliberately fails if the table already exists; do not rerun it against an existing schema without reviewing it.
3. Use the project URL and **publishable** key from the project settings. Add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` to the workspace-root `.env`, preserving existing entries. Connection helpers load that file automatically; process environment variables take precedence. `.env.example` documents the names. Database passwords and secret keys are not needed for this Data API connection.
4. Enable the intended Supabase Auth sign-in method and create two development test users. The adapter requires a user's access token; a publishable key alone does not authorize document access.
5. Verify with user A: insert and retrieve a document. Verify with user B: A's document must not appear in a list or by ID. Requests without a user token must not read or create documents. Update/delete should be denied in this checkpoint.

Ownership comes from `auth.uid()`, not a client-provided owner ID. Row-level security lets each authenticated user read and insert only their own rows. The adapter uses a publishable key plus a user token and rejects new secret keys. Do not replace it with a secret/service-role key, which would bypass these policies.

No cloud changes have been applied from this repository. Current tests use mocked HTTP responses, so they verify request construction, response parsing, error handling, pagination, and preservation of long Unicode text; they do **not** prove live database access or RLS isolation. The adapter is not exposed through HTTP routes yet. Upload limits, passage splitting, original-file storage, and authentication wiring are still upcoming.

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

`storage/connection.py` provides `open_document_store(access_token)`, which loads configuration and manages a user-scoped HTTP client for the adapter. It is ready for future authenticated routes. `/health` remains independent of Supabase so the API can still start without cloud configuration. Connection helpers use direct HTTPS with a 15-second diagnostic timeout and do not use environment proxies.

References: [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys), [row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security), and [database and Storage architecture](https://supabase.com/docs/guides/getting-started/the-supabase-way-to-build).

## Checkpoint 1: what happens

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
