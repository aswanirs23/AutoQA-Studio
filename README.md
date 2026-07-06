# AutoQA Studio

**Project-based** persistent manual test case generation with a **plugin parser architecture**. Each project has an editable **description** (used as AI context) and multiple **features** containing **test cases**, all stored in SQLite. Inputs include manual text, Figma URLs, Jira issues, screenshots (vision), and browser session recordings (via Playwright or Cursor IDE Browser MCP). Exports to **Excel**, **CSV**, **JSON**, or **TestRail-style CSV** with optional filtering by feature, priority, and search term. Test cases support **source traceability** (`source_ref`), full CRUD, stats, and input history.

## UI

The single-page frontend (Tailwind CSS + vanilla JS) provides:

- **Project gallery** — create, switch, edit, and delete projects
- **Sidebar navigation** — Project Overview, Test Cases, Dashboard, Settings
- **Project Overview** — editable description, AI-generated description from uploaded documents (PDF, TXT, DOCX, XLSX)
- **Test Cases** — Jira-backlog-style feature accordions with per-feature Generate, Iterate, and Delete actions; click a test case for a detail/edit slide-in; export modal with filtering
- **Dashboard** — metric cards, Chart.js doughnut/bar charts (by type, feature, priority), generation history timeline
- **Settings** — inline LLM and integration key management (stored in SQLite, masked display)
- **Dark mode** — toggle in the header, persisted in localStorage

## Requirements

- Python 3.11+
- At least one LLM API key: **OpenAI**, **Anthropic**, and/or **Google Gemini**

## Setup

```bash
cd testgen-ai
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env          # then edit .env
```

### Environment variables

**Non-secret configuration** (set in `.env`):

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | Default provider: `openai`, `anthropic`, or `gemini` |
| `OPENAI_MODEL` | Default `gpt-4o` |
| `ANTHROPIC_MODEL` | Default `claude-sonnet-4-20250514` |
| `GEMINI_MODEL` | Default `gemini-2.0-flash` |
| `DATABASE_PATH` | SQLite file (default `data/testgen.db`) |
| `AUTH_DISABLED` | `true` = no login required (default). `false` = JWT auth enabled |
| `JWT_SECRET` | Required when `AUTH_DISABLED=false` |

**API keys / secrets** (configured via the Settings UI, stored in SQLite — `.env` values for these are ignored):

| Key | Purpose |
|-----|---------|
| OpenAI API key | GPT-4o (text + vision) |
| Anthropic API key | Claude (text + vision) |
| Google Gemini API key | Gemini (text + vision) |
| Figma access token | Figma REST API |
| Jira base URL | e.g. `https://your-domain.atlassian.net` |
| Jira email | Jira account email |
| Jira API token | Atlassian API token |

If the default provider's key is not configured, the app **automatically falls back** to another configured provider.

## Run

```bash
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080
```

Open **http://127.0.0.1:8080/** for the web UI, or **http://127.0.0.1:8080/docs** for OpenAPI.

The UI loads parser tabs (Manual text, Figma, Jira, Screenshot, Browser Session) from `GET /api/parsers`. If you run the frontend from a separate dev server (e.g. Vite on 5173, Live Server on 5500), the script auto-detects the API at `http://127.0.0.1:8080`, or set `localStorage.tcg_api_base` manually.

### Quick start (Linux/macOS)

After the one-time `python -m venv .venv` step, [start.sh](start.sh) wraps the activate → install → build CSS → uvicorn sequence:

```bash
./start.sh
```

It activates `.venv`, installs `requirements.txt` + npm deps, rebuilds Tailwind CSS, and launches uvicorn on `0.0.0.0:8080`. Run it from the project root.

## Deployment

The Run section above is local-dev. For a deployable build use the steps here.

### Production environment variables

Set these in your deployment environment (or in `.env` on the host):

| Variable | Production value | Notes |
|----------|------------------|-------|
| `LOG_LEVEL` | `INFO` | `DEBUG` while bringing up a new env, then drop to `INFO` |
| `CORS_ORIGINS` | `https://your-domain.example` | Comma-separated allowlist. Don't ship `*`. Same-origin (FastAPI serves the UI itself) means CORS isn't strictly required, but lock it anyway |
| `DATABASE_PATH` | `/app/data/testgen.db` | Mount a persistent volume at `/app/data` |
| `AUTH_DISABLED` | `true` for now | Flip to `false` once a login UI is added; until then keep behind a private network |
| `JWT_SECRET` | (any long random) | Required when `AUTH_DISABLED=false` |
| `LLM_PROVIDER` | `openai` / `anthropic` / `gemini` | Default provider. Per-provider API keys go in the **Settings UI** (SQLite), not env vars |

### Build the production Tailwind CSS

The dev build loads Tailwind from a browser CDN (JIT in the browser). For production, build a minified CSS file once.

**With Node** (recommended):

```bash
npm install
npm run build:css
```

This produces `frontend/styles-tailwind.css` (minified). Repeat after any change to `index.html` or `app.js` that introduces new Tailwind utility classes. During UI work, `npm run watch:css` rebuilds on save.

**Without Node** — use the standalone Tailwind v4 CLI binary from [Tailwind's GitHub releases](https://github.com/tailwindlabs/tailwindcss/releases) (single executable, no Node required):

```bash
./tailwindcss -i ./frontend/tailwind.css -o ./frontend/styles-tailwind.css --minify
```

### Docker (one-shot build & run)

The provided `Dockerfile` is multi-stage: a Node stage builds the Tailwind CSS, then a slim Python stage runs uvicorn. No Node toolchain is required on the host.

```bash
docker build -t testcase-agent .
docker run --rm -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  testcase-agent
```

On Windows PowerShell:

```powershell
docker run --rm -p 8080:8080 `
  --env-file .env `
  -v ${PWD}/data:/app/data `
  testcase-agent
```

The mounted `data/` volume persists the SQLite DB across container restarts. Health: `GET http://localhost:8080/health` returns `{"status":"ok"}`.

### Hosting notes

- **Bind to `0.0.0.0`** in any non-Docker run on a server: `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080`. The dev `127.0.0.1` only accepts localhost connections.
- **Single uvicorn worker.** SQLite + multiple workers can corrupt writes. If you outgrow this, migrate to Postgres before scaling workers.
- **Put a reverse proxy (nginx / Caddy / Traefik) in front** for TLS, gzip, and request size limits. Point it at `http://app:8080`.
- **Persist `/app/data`** — the SQLite DB and any uploaded artifacts live there. Lose this volume, lose all projects and test cases.
- **Health check endpoint**: `GET /health` for platform liveness probes (Render, Fly, Railway, K8s).

## API overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Register (when `AUTH_DISABLED=false`) |
| POST | `/api/auth/login` | Login → JWT |
| GET | `/api/auth/me` | Current user |
| POST | `/api/projects` | Create project `{ "name", "description" }` |
| GET | `/api/projects` | List projects |
| GET | `/api/projects/{id}` | Project + features |
| PUT | `/api/projects/{id}` | Update project `{ "name", "description" }` |
| DELETE | `/api/projects/{id}` | Delete (cascade) |
| POST | `/api/projects/{pid}/generate-description` | Upload file → AI-generated project description |
| POST | `/api/projects/{pid}/features` | Create feature |
| GET | `/api/projects/{pid}/features` | List features |
| PUT | `/api/projects/{pid}/features/{fid}` | Update feature |
| DELETE | `/api/projects/{pid}/features/{fid}` | Delete feature (cascade) |
| GET | `/api/projects/{pid}/test-cases?feature_id=` | List test cases |
| PATCH | `/api/projects/{pid}/test-cases/{tc_id}` | Update test case fields |
| DELETE | `/api/projects/{pid}/test-cases/{tc_id}` | Delete one test case |
| POST | `/api/projects/{pid}/test-cases/bulk-delete` | Body `{ "ids": ["TC_001", ...] }` |
| GET | `/api/projects/{pid}/stats` | Counts by type, priority, feature |
| GET | `/api/projects/{pid}/input-history?limit=` | Recent generation runs |
| GET | `/api/parsers` | Parser metadata (dynamic UI) |
| POST | `/api/generate` | JSON or multipart — optional `min_test_cases`, `preferred_test_types` |
| POST | `/api/generate/iterate` | Instruction + optional `feature_id`, `type_filter`, `min_test_cases`, `preferred_test_types` |
| GET | `/api/export/{project_id}?format=&feature_ids=&search=&priority=` | Export with filters |
| GET | `/api/settings/keys` | API key status (configured, masked) |
| PUT | `/api/settings/keys` | Save or clear API keys |
| POST | `/api/browser-session/start` | Create browser session |
| GET | `/api/browser-session/{id}` | Get session details |
| POST | `/api/browser-session/{id}/step` | Add recorded step |
| POST | `/api/browser-session/{id}/complete` | Complete/fail session |

Send `Authorization: Bearer <token>` when `AUTH_DISABLED=false`.

### `POST /api/generate`

**JSON** (no file): set `input_type` to one of the built-in parsers (see table below).

```json
{
  "input_type": "text",
  "project_id": "<uuid>",
  "feature_id": "<uuid>",
  "data": { "feature_name": "login", "content": "..." },
  "llm_provider": "openai"
}
```

**Multipart** (image upload / **screenshot** parser): `input_type=screenshot`, `project_id`, `feature_id`, `data` (JSON string), `file` (image), optional `llm_provider`, `llm_model`, `min_test_cases`, `preferred_test_types`.

**Multiple sources in one request**: send `inputs` instead of `input_type` / `data`. Each item has `input_type`, `data`, and optionally `file_index` when using multipart files.

```json
{
  "project_id": "<uuid>",
  "feature_id": "<uuid>",
  "inputs": [
    { "input_type": "text", "data": { "feature_name": "checkout", "content": "User must confirm email." } },
    { "input_type": "jira", "data": { "issue_key": "PROJ-42" } }
  ]
}
```

### Built-in parsers (`GET /api/parsers`)

| `input_type` | Module | Purpose | Extra configuration |
|--------------|--------|---------|---------------------|
| `text` | `text_parser.py` | Paste requirements / free text | None (LLM keys only) |
| `figma` | `figma_parser.py` | Figma file or frame URL → structure + text | Figma access token |
| `jira` | `jira_parser.py` | Fetch issue by key (REST API) | Jira base URL, email, API token. Optional `include_linked` |
| `screenshot` | `screenshot_parser.py` | Image upload → vision summary → tests | Multipart `file`; uses provider's vision API |
| `browser_session` | `browser_session_parser.py` | Record a browser session → tests | Playwright or IDE Browser MCP |

### `POST /api/generate/iterate`

```json
{
  "project_id": "<uuid>",
  "instruction": "Add more edge cases for validation",
  "feature_id": "<uuid>",
  "type_filter": "edge",
  "min_test_cases": 5,
  "preferred_test_types": ["edge", "negative"],
  "llm_provider": "anthropic",
  "llm_model": "claude-sonnet-4-20250514"
}
```

### Browser Session Recording (`POST /api/browser-session/*`)

The **Browser Session** input type lets you record a real browser interaction and generate test cases from the recorded flow. It works with either the **Playwright MCP** or the **Cursor IDE Browser MCP**.

**Two-phase flow:**

1. **Record** — Create a session, execute steps, capture results.
2. **Generate** — Feed the completed session into `POST /api/generate` with `input_type: "browser_session"`.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/browser-session/start` | Create a session `{ project_id, url, feature_name, browser_type, steps: string[] }`. Each string in `steps[]` becomes the `instruction` of a pending step. |
| GET | `/api/browser-session/{id}` | Retrieve session with all recorded steps |
| GET | `/api/browser-session/project/{pid}` | List sessions for a project |
| POST | `/api/browser-session/{id}/step` | Add a recorded step (full object) `{ instruction, action_type, target, value, snapshot_yaml, screenshot_b64, vision_description, status }` |
| PUT | `/api/browser-session/{id}/step/{index}` | Update a step by index |
| POST | `/api/browser-session/{id}/complete` | Mark session as `completed` or `failed` |

**Agent-mediated workflow (recommended):**

The Cursor agent orchestrates the recording by calling MCP tools:

1. User opens the Generate modal, selects **Browser Session** tab, enters URL and steps.
2. Frontend calls `POST /api/browser-session/start` to create the session with pending steps.
3. The Cursor agent reads the session, then for each step:
   - Calls `browser_navigate` (Playwright or IDE Browser MCP) to open the URL
   - Calls `browser_snapshot` to capture the accessibility tree
   - Interprets the step instruction and calls the appropriate MCP tool (`browser_click`, `browser_type`, etc.)
   - Calls `browser_take_screenshot` to capture the result
   - Optionally describes the screenshot via a vision model
   - Posts the step result back via `POST /api/browser-session/{id}/step`
4. Agent calls `POST /api/browser-session/{id}/complete` when done.
5. User clicks **Generate test cases** — the `browser_session` parser reads the recorded session and the LLM generates test cases from the full context (steps, snapshots, screenshots).

**Frontend UX:**

The Browser Session tab in the generate modal provides:
- URL input and step instructions (one per line)
- Browser type selector (Playwright / IDE Browser)
- "Start Recording" button to create the session
- Live step progress with status indicators
- Interactive "Add Step" input during recording
- "Complete Recording" button to finalize

## Auto-execute test cases (Playwright)

The **Auto-execute** button on any test case detail turns it into a runnable Playwright test:

1. Set a **Base URL** in Project Overview (e.g. `https://your-app.example.com`).
2. Open a test case detail and click **Auto-execute**.
3. An LLM generates Playwright Python code for the case. You can review and edit the code in the modal before running.
4. Toggle **Watch it run** for a visible (headed) Chromium window — useful for live demos and debugging. Leave it off (headless) for normal runs.
5. Click **Run** — the server spawns a Chromium subprocess against your Base URL, executes the test, and returns pass / fail / error with a screenshot and console output.

The test case row in the feature accordion gets a small status dot (green / red / amber) reflecting the most recent run.

### When a test fails because the wording diverged

If the app's actual behavior is correct but the test's `expected_result` wording is stale, click **✓ Mark as expected behavior** in the result panel. This opens a small modal:

- The **original expected result** (read-only) and the **page text we observed** are shown side-by-side for reference.
- An LLM proposes a rewritten `expected_result` that documents the observed behavior. Click **🔄 Try another phrasing** for a different draft, or edit the textarea directly.
- Optionally tick **Regenerate Playwright code from new expected** if you want fresh code; leave it unchecked to keep code you've already edited by hand.
- Click **Save (replaces expected result)** — the test case is patched and the test re-runs immediately. If the re-run still fails, a small note appears suggesting that the test logic itself may need a manual edit.

The button only appears for **FAILED** results — it's hidden on `passed` (no need) and `error` (runner-level crash, not an assertion divergence).

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/projects/{pid}/test-cases/{tcid}/generate-playwright` | LLM-generate Playwright Python code from the manual case. Returns `{ code }`. |
| POST | `/api/projects/{pid}/test-cases/{tcid}/run-playwright` | Body `{ code, headless }`. Returns `{ status, screenshot_b64, error_message, console_log, duration_ms }`. Persists the result on the test case row. |
| POST | `/api/projects/{pid}/test-cases/{tcid}/suggest-expected-result` | Body `{ actual_page_text, current_expected_result, error_message }`. LLM-rewrites the expected result based on observed behavior. Returns `{ suggested }`. Does NOT persist — use the existing PATCH endpoint to save. |
| PUT | `/api/projects/{pid}` | Now also accepts `base_url`. |

### Security boundaries

- Generated code is checked against a regex denylist (`subprocess`, `os.system`, `eval`, `exec`, `__import__`, `open(`, `urllib`, etc.) at both generate-time and run-time.
- The subprocess has a 60-second wall-clock timeout and is killed on overrun.
- The subprocess inherits the parent environment minus any var whose name contains `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `JWT`, or `PRIVATE_KEY` — so the user's stored LLM keys can't leak into generated test code.
- Auth-required flows are out of scope: the Base URL must be either public or already-authenticated state.

### Requirements

- `playwright install chromium` on the host (the Playwright Python package is already in `requirements.txt`; the Chromium binary is a separate one-time install).

## Adding a new input parser (plugin)

1. Create `backend/services/parsers/your_parser.py`.
2. Subclass `BaseParser`, set `meta`, implement `async def parse(self, data, file) -> ParsedInput`.
3. `ParserRegistry.register(YourParser())`.
4. Import the module in `backend/services/parsers/__init__.py`.

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

## License

Aswani
