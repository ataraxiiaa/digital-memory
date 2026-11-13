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

Checkpoint 1 provides `GET /health` and interactive API documentation. Supabase PostgreSQL storage now has an applied document-table migration, verified catalog permissions, and a tested storage adapter. Two-user isolation checks and authenticated document routes are still pending. See [backend instructions and the five-step roadmap](src/backend/README.md) for setup and the remaining authentication, ingestion, search, and frontend work. SQLite is not used.

Connection helpers now load `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` from the workspace-root `.env` or the process environment. Run `.\.venv\Scripts\python.exe -m src.backend.check_supabase` for a read-only connectivity diagnostic; add `--documents` to check a signed-in user's document access using a hidden token prompt. Live checks require your project configuration.

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

The interface includes an overview, searchable documents, collection filters, a timeline, original passage previews, and session-only saving. All people and documents are explicitly labeled synthetic sample data. Saved items reset on reload. No API, authentication, file imports, or persistence is connected.

Pages and responsive styles live in `src/frontend/app/`; sample documents and search helpers live in `src/frontend/lib/`. Tests under `tests/frontend/` cover search, combined filters, empty results, ordering, and date formatting using Node's built-in test runner. No separate lint tool is configured. No image assets are required; the interface uses CSS and system fonts.

## Learning approach

Continue in small verified checkpoints. The frontend remains independent until the document API is ready.

## Private data

Three downloaded long-text fixtures are available under `tests/fixtures/long-documents/`: Pride and Prejudice, Moby Dick, and War and Peace. That folder includes source links, rights notes, checksums, sizes, and future ingestion checks. These published texts are separate from the synthetic notes and are not loaded by the frontend.

Ten original synthetic source documents are available under `tests/fixtures/documents/`. See that directory's README for the metadata format and expected search/timeline scenarios. They are development fixtures for a future backend and are not yet loaded by the frontend.

Use synthetic documents during development. Keep secrets in ignored local environment files and private imports under ignored `data/` or `uploads/` directories. Never commit real personal documents or credentials.
