# Repository Guidelines

## Project Structure & Module Organization

This workspace currently contains only `.venv/`, a local Python virtual environment. No application source, tests, assets, or dependency manifest is present. Treat `.venv/` as generated tooling; do not edit its installed packages or commit its contents.

When adding the initial implementation, organize application code under `src/` and automated tests under `tests/`. Add assets only when needed, and document their location in `README.md`. These directories are proposed conventions, not existing project structure.

## Build, Test, and Development Commands

Run commands from the workspace root in PowerShell:

- `.\.venv\Scripts\Activate.ps1` activates the existing Python environment.
- `.\.venv\Scripts\python.exe --version` checks the environment's Python version without activation.

No build, application startup, lint, or test command is configured yet. When introducing tooling, record the exact installation and execution commands in `README.md` and declare dependencies in a project manifest. Avoid relying on undocumented packages already installed in `.venv/`.

## Coding Style & Naming Conventions

For new Python code, use four-space indentation, `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep modules focused and use descriptive names. No formatter or linter configuration currently exists; document any tool adopted with the first implementation.

## Testing Guidelines

No test framework or coverage threshold is established. Add tests alongside new behavior and document how to run them. If adopting pytest, place tests in `tests/test_<module>.py` and name test functions `test_<behavior>`. Cover normal behavior, edge cases, and failure paths.

## Commit & Pull Request Guidelines

This directory has no Git repository or commit history from which to infer conventions. When version control is initialized, use concise, imperative commit subjects such as `Add document ingestion parser`. Pull requests should explain the change, list validation performed, and link relevant issues. Include screenshots for user interface changes.

## Security & Configuration

Keep secrets, local configuration, and generated environments out of version control. Provide placeholder configuration examples when adding external services.