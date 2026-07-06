# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product name vs. paths

Display name is **AutoQA Studio**. The repo directory (`testgen-ai`), default SQLite filename (`data/testgen.db`), and the FastAPI module path (`backend.main:app`) deliberately keep the legacy `testgen` name — do not rename them.

## Commit conventions

Do **not** add a `Co-Authored-By: Claude` (or any AI co-author) trailer to commit messages. Commits are attributed solely to the repository owner.

## Commands

```bash
# One-shot dev server (Linux/macOS): activates .venv, installs deps, builds Tailwind, runs uvicorn on 0.0.0.0:8080
./start.sh

# Manual dev server (cross-platform)
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080

# Tailwind CSS (production build / watch during UI work)
npm run build:css
npm run watch:css

# Tests
python -m pytest                                          # full suite
python -m pytest tests/services/test_source_ref.py        # one file
python -m pytest tests/audit/test_playwright_runner.py::test_name  # one test
python -m pytest -k "browser_session"                     # by keyword

# Playwright Chromium (one-time, required for the auto-execute feature)
playwright install chromium
```

`pytest.ini` sets `asyncio_mode = auto`, so async tests work without per-test decorators.

## Architecture

**Single-process FastAPI app that serves both the REST API (`/api/*`) and the static SPA from `frontend/`** (`backend/main.py` mounts `StaticFiles` at `/`). One uvicorn worker only — SQLite writes will corrupt under multiple workers. If scaling becomes necessary, migrate to Postgres before increasing worker count.

**Data layer** is SQLite via `aiosqlite` ([backend/db.py](backend/db.py)). Schema is defined inline as a single DDL string, applied idempotently on startup. **All schema changes must go through `migrate_schema()`** using the additive `_ensure_column()` pattern — never edit historical tables in-place, since users have existing DBs. Repositories under [backend/repositories/](backend/repositories/) own all SQL; routers never write raw SQL.

**API key storage is intentionally split**: non-secret config (default models, provider choice, `DATABASE_PATH`, `AUTH_DISABLED`) comes from `.env` via `pydantic-settings`. **LLM / Figma / Jira API keys are stored only in the `app_settings` SQLite table** and managed through the Settings UI. `.env` values for the keys in `SECRET_OVERRIDABLE_KEYS` ([backend/config.py](backend/config.py)) are deliberately ignored at runtime — `get_effective_settings()` strips them and merges in the in-memory `SECRET_OVERRIDES` dict that is hydrated from SQLite at startup and after every `PUT /api/settings/keys`. Do not introduce code that reads those secrets from env.

**LLM provider abstraction** lives in [backend/services/llm_service.py](backend/services/llm_service.py) with OpenAI, Anthropic, and Gemini backends. `effective_llm_provider()` in `config.py` implements automatic fallback: if a client omits the provider and the configured default's key is missing, it walks `openai → gemini → anthropic`. When a client *does* specify a provider, that choice is honored exactly (no fallback). Vision uses the same provider as text.

**Plugin parser architecture** ([backend/services/parsers/](backend/services/parsers/)) is the extension point for new input sources. Each parser:
1. Subclasses `BaseParser` with a `ParserMeta` (drives the dynamic UI form via `GET /api/parsers`).
2. Implements `async def parse(self, data, file) -> ParsedInput` — `ParsedInput` is the **only** shape the LLM layer sees, so keep it stable.
3. Calls `ParserRegistry.register(...)` at module load.
4. Is imported from [backend/services/parsers/__init__.py](backend/services/parsers/__init__.py) — **import order there determines tab order in the UI**.

Built-in parsers: `text`, `figma`, `jira`, `screenshot` (vision), `browser_session`. The `screenshot` parser stashes raw bytes under metadata keys starting with `_` — `strip_internal_metadata()` (in `parsers/base.py`) drops those before anything is logged or persisted.

**Generation pipeline**: `POST /api/generate` (and `/iterate`) accepts either a single `input_type` or an `inputs[]` array. Multi-source requests run each parser and call `merge_parsed.py` to combine `ParsedInput`s before the LLM call. Test cases are deduped project-wide by SHA-256 hash ([dedup_service.py](backend/services/dedup_service.py)) and tagged with a `source_ref` for traceability.

**Browser session recording → test generation** is a two-phase flow (see README "Browser Session Recording"). Sessions live in `browser_sessions` table; the Cursor/IDE agent drives MCP browser tools, posts step results back via `POST /api/browser-session/{id}/step`, and once `complete`d the `browser_session` parser feeds the entire recorded flow to the LLM. The independent `backend/services/browser_explorer/` subsystem is a separate AI-driven exploration loop (orchestrator + critic + budget) that drives a browser autonomously — distinct from the user-guided session recording flow.

**Auto-execute (Playwright)** ([backend/services/playwright_runner.py](backend/services/playwright_runner.py)) takes LLM-generated Python code and runs it in a subprocess. **Security boundaries are not optional**: a regex denylist (`subprocess`, `os.system`, `eval`, `exec`, `__import__`, `open(`, `urllib`, …) runs at both generate-time and run-time; the subprocess has a 60-second wall-clock timeout; and the environment is scrubbed of any var whose name contains `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `JWT`, or `PRIVATE_KEY` so user-stored LLM keys cannot leak into generated test code. Preserve these guards when touching the runner.

**Auth** is bypassed by default (`AUTH_DISABLED=true` → a built-in local user). Flip to `false` for multi-user, but you must also set `JWT_SECRET`. The README's "Hosting notes" section is the source of truth for deployment.

## Frontend

Single-page app in [frontend/](frontend/): one HTML file, one `app.js`, Tailwind CSS. In dev the Tailwind CDN provides JIT in the browser; in production `npm run build:css` (or the standalone Tailwind v4 CLI) emits the minified `frontend/styles-tailwind.css` that the Dockerfile's stage-1 also produces. Re-run the build after introducing new Tailwind utility classes in `index.html` or `app.js`.

The frontend auto-detects the API at `http://127.0.0.1:8080` when served from a separate dev origin (Vite, Live Server); override via `localStorage.tcg_api_base`.
