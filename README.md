# Digital Memory

A learning project that turns explicitly authorized notes and files into a searchable timeline and relationship graph, with evidence linked to original passages.

The MVP targets up to 100 synthetic text documents. Mailbox and cloud-source integrations are outside the initial scope.

## Phase 1: Foundations

We work through one small checkpoint at a time:

- [x] Part 1: Initialize the local Git repository and document the plan.
- [ ] Part 2: Create a minimal FastAPI backend with a health endpoint.
- [ ] Part 3: Create a minimal Next.js frontend.
- [ ] Part 4: Fetch backend health from the frontend and display connection status.

## Planned structure

```text
src/
  backend/    # Python API and application logic
  frontend/   # Next.js interface
tests/        # Automated tests, added with relevant behavior
```

These application directories will be created in the next parts. `.venv/` is an existing local Python environment and is excluded from Git.

## Local tools

Run commands from the repository root in PowerShell:

```powershell
git status
node --version
npm.cmd --version
.\.venv\Scripts\python.exe --version
```

Use `npm.cmd` if PowerShell execution policy blocks `npm.ps1`. Python can be invoked directly without activating the environment.

No application dependencies or start commands are configured yet. Each setup part will add its dependency manifest and exact run instructions.

## Learning approach

For each part, explain the purpose, introduce a small change, verify its behavior, and review what happened before proceeding.

## Private data

Use synthetic documents during development. Keep secrets in ignored local environment files and private imports under ignored `data/` or `uploads/` directories. Never commit real personal documents or credentials.
