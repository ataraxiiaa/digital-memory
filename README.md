# Digital Memory

A learning project that turns explicitly authorized notes and files into a searchable timeline and relationship graph, with evidence linked to original passages.

The MVP targets up to 100 synthetic text documents. Mailbox and cloud-source integrations are outside the initial scope.

## Phase 1: Foundations

We work through one small checkpoint at a time:

- [x] Part 1: Initialize the local Git repository and document the plan.
- [x] Part 2: FastAPI foundation with a health endpoint.
- [x] Part 3: Create a standalone Next.js frontend.
- [ ] Part 4: Backend integration (deferred).

## Planned structure

```text
src/
  backend/    # Python API and application logic
  frontend/   # Next.js interface
tests/        # Automated tests, added with relevant behavior
```

The frontend lives under `src/frontend/`; the API foundation lives under `src/backend/`. `.venv/` is an existing local Python environment and is excluded from Git.

## Backend progress

Checkpoint 1 provides `GET /health` and interactive API documentation. Supabase PostgreSQL storage has an applied migration, verified catalog permissions, and authenticated document create/list/read endpoints. Live two-user isolation checks remain pending. See [backend instructions and the five-step roadmap](src/backend/README.md) for API examples and the remaining ingestion, search, and frontend work. SQLite is not used.

Connection helpers now load `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` from the workspace-root `.env` or the process environment. Run `.\.venv\Scripts\python.exe -m src.backend.check_supabase` for a read-only connectivity diagnostic; add `--documents` to check a signed-in user's document access using a hidden token prompt. Live checks require your project configuration.

`GET /auth/me` verifies a Supabase user access token and returns the signed-in user's basic identity. It is available through the bearer-token **Authorize** flow at `/docs`, alongside `POST /documents`, `GET /documents`, and `GET /documents/{document_id}`. `/health` stays public. The frontend has email/password sign-in, session restoration and refresh, backend identity verification, and local sign-out. Document UI integration and live two-user ownership verification remain upcoming.

## Local tools

Run commands from the repository root in PowerShell:

```powershell
git status
node --version
npm.cmd --version
.\.venv\Scripts\python.exe --version
```

Use `npm.cmd` if PowerShell execution policy blocks `npm.ps1`. Python can be invoked directly without activating the environment.

The frontend uses Next.js App Router, React, and TypeScript. Its dependencies are declared in `src/frontend/package.json` and locked in `package-lock.json`. Use Node.js 24 LTS.

From the workspace root:

```powershell
cd src/frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:3000. For validation and production serving, run from `src/frontend/`:

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
npm.cmd start
```

The signed-in workspace includes private PDF upload, a live owned-document list with pagination, and metadata/text details. No bundled example documents or invented user identity are displayed. Timeline extraction is not implemented. See [frontend configuration and upload instructions](src/frontend/README.md). Both frontend and backend must run.

Pages and responsive styles live in `src/frontend/app/`; authentication components and the Supabase client live under `src/frontend/components/` and `src/frontend/lib/`. Playwright tests under `tests/frontend/e2e/` cover authentication and the empty workspace. No separate lint tool is configured. No image assets are required; the interface uses CSS and system fonts.

## Learning approach

Continue in small verified checkpoints. The frontend remains independent until the document API is ready.

## Private data

The bundled synthetic notes and downloaded book fixtures have been removed. Documents will be explicitly added by their users through the upcoming ingestion flow. Account creation is available at `/sign-up`, including email-confirmation handling and a link back to sign-in.

Authenticated PDF storage is available through `POST /documents/upload` and the frontend PDF picker. It accepts PDFs up to 30 MiB, stores originals privately, and saves linked metadata with `status: uploaded`. The frontend shows transfer progress, saving states, errors, and automatic list refresh. Original PDF viewing, text extraction, and OCR remain upcoming. See the backend README for validation, limits, policies, and failure cleanup.

Use synthetic documents during development. Keep secrets in ignored local environment files and private imports under ignored `data/` or `uploads/` directories. Never commit real personal documents or credentials.
