# Architecture

Single-process FastAPI app that serves both the REST API (`/api/*`) and the static SPA from `frontend/`. Data is SQLite via `aiosqlite`. Run with **one** uvicorn worker — SQLite writes corrupt under multiple workers.

## Project layout

- `backend/main.py` — FastAPI app, lifespan DB init, static frontend
- `backend/db.py` — SQLite schema + `get_db()`
- `backend/config.py` — Settings, env vars, effective settings merge
- `backend/repositories/` — project, feature, testcase, input, settings, user repos
- `backend/routers/` — `projects`, `features`, `generate`, `export`, `extract` (document → text for project context), `playwright_exec` (auto-execute endpoints), `browser_session`, `parsers_meta`, `settings_keys`, `auth`
- `backend/services/parsers/` — plugins: `text_parser`, `figma_parser`, `jira_parser`, `screenshot_parser`, `browser_session_parser`, plus `base`, `registry`, `merge_parsed`
- `backend/services/browser_session.py` — Browser session CRUD (create, add steps, complete)
- `backend/services/browser_explorer/` — AI-driven browser exploration (orchestrator, critic, ledger, budget, value_gen, drivers for Playwright / IDE Browser MCP)
- `backend/services/llm_service.py` — OpenAI / Anthropic / Gemini, including Playwright code generation
- `backend/services/llm_tool_loop.py` — Multi-turn tool-call loop for agentic LLM flows
- `backend/services/playwright_runner.py` — Sandboxed subprocess runner for auto-execute (denylist, env scrubbing, 60s timeout)
- `backend/services/dedup_service.py` — SHA-256 dedup (project-wide)
- `backend/services/source_ref.py` — Test case source traceability helpers
- `backend/services/settings_store.py` — Load/persist UI API key overrides
- `backend/services/upstream_errors.py` — Map LLM / integration errors to HTTP responses
- `backend/prompts/templates.py` — LLM prompt templates
- `frontend/` — SPA (Tailwind CSS via CDN in dev, prebuilt `styles-tailwind.css` in prod; Chart.js, vanilla JS)
- `start.sh` — Convenience launcher (Linux/macOS): activate venv → install deps → build CSS → run uvicorn on 8080

For contributor-facing conventions and deeper architecture notes, see [CLAUDE.md](../CLAUDE.md).
