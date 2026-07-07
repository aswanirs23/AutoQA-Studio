# Authenticated auto-execute — design

**Date:** 2026-07-07
**Status:** approved, ready for implementation planning

## Problem

Auto-execute (Playwright) today runs generated test code against a project's
`base_url` in a fresh, **unauthenticated** browser context. Real applications put
almost everything behind a login, so most test cases can't actually run. The runner
also scrubs `TOKEN`/`PASSWORD`/`SECRET` from the subprocess environment, so there is
no safe way to feed credentials to a test, and each run is independent (no shared
session). See [../../limitations.md](../../limitations.md) fix #1.

## Goal

Let a user set up login **once per project** so that every auto-execute run starts
already authenticated. No credentials appear in generated test code. Reuse a captured
session (Playwright `storage_state`) across runs, and re-establish it automatically
when it expires.

## Scope

**In scope (v1):**
- Per-project login configuration (login URL, username, password, optional selector
  overrides).
- A "Test login & save session" action that logs in once and persists the session.
- Auto-execute runs use the saved session (authenticated context).
- Automatic single re-login + retry when a run detects an expired session.

**Out of scope (follow-ups):**
- DOM-aware code generation (limitations.md fix #2).
- Multi-step / MFA / SSO / OAuth login flows — v1 handles a single form submit.
- Cross-browser (Chromium only, unchanged).
- Encryption of credentials at rest (matches current API-key storage; noted as a
  known limitation).

## Approach

Chosen: **store credentials + auto-login, reusing a captured `storage_state`.**

Rejected alternatives:
- *Import session JSON only (no credentials):* simplest and most secure, but the user
  must manually re-import on every expiry; no auto-refresh. Poorer real-world UX.
- *LLM-generated login from a live DOM snapshot:* most adaptive, but couples this work
  to fix #2 and adds an LLM call/cost to setup. Deferred.

Field detection: **auto-detect with optional overrides** — reuse the role/placeholder
heuristics already encoded in the Playwright code-gen prompt (username/email field,
password field, submit button); allow explicit CSS selector overrides per project for
unusual forms.

## Data model

Add one additive JSON column to `projects` via the existing `_ensure_column` pattern in
[`backend/db.py`](../../../backend/db.py):

```
ALTER TABLE projects ADD COLUMN auth_config TEXT NOT NULL DEFAULT '{}'
```

Shape of `auth_config`:

```json
{
  "login_url": "https://app.example.com/login",
  "username": "standard_user",
  "password": "•••• (stored plaintext in SQLite, like API keys)",
  "selectors": { "username": "#email", "password": "#pass", "submit": "button[type=submit]" },
  "success_check": "/dashboard",
  "verified_at": "2026-07-07T10:00:00Z",
  "last_error": ""
}
```

- `selectors`, `success_check` are optional. `success_check` is a path or visible-text
  string used to confirm login worked (default: "landed somewhere other than
  `login_url`").
- Storage/security: credentials live only in SQLite — **the same posture as the
  existing LLM/Figma/Jira API keys** (DB only, never in `.env`, gitignored `data/`,
  masked in the UI, never returned in full via the API, never in any LLM prompt, never
  written into generated test code). Not encrypted at rest — documented in
  limitations.md.

## Session (storage_state)

- On "Test login & save session", the backend runs the login flow in the **sandboxed
  subprocess** and, on success, writes Playwright's `storage_state` to
  `data/auth/<project_id>.json` (under the already-gitignored `data/`).
- The login script is **assembled server-side** — it is not user/LLM code:
  1. `page.goto(login_url)`
  2. fill username field (override selector, else heuristic: placeholder/role
     `email`/`username`)
  3. fill password field (override selector, else `input[type=password]`)
  4. click submit (override selector, else role=button submit / `button[type=submit]`)
  5. wait for navigation / `success_check`
  6. `context.storage_state(path=...)`
- Credentials are injected into this ephemeral script server-side; the script lives only
  in the subprocess tempdir and is never returned to the client.

## Runner changes

[`backend/services/playwright_runner.py`](../../../backend/services/playwright_runner.py):

- `run_playwright_code(code, base_url, headless, storage_state_path=None)` — when a path
  is given and the file exists, the wrapper builds the context with
  `browser.new_context(storage_state=<path>)`; otherwise unchanged.
- New `capture_login_session(auth_config, base_url) -> {ok, screenshot_b64, error}` —
  runs the server-assembled login script in the same sandboxed subprocess and writes the
  state file. Reuses the URL validation and timeout machinery.
- The wrapper template (`playwright_runner_wrapper.py.tmpl`) is parameterized to
  optionally load `storage_state`. A separate login wrapper (or a mode flag) handles the
  capture flow.
- **Auto-relogin:** after a run, if the result indicates an expired session (final URL
  matches `login_url`, or a password field is present on the page), the runner
  re-captures the session once and retries the test a single time before reporting.

## API

- `PUT /api/projects/{id}/auth` — body = auth_config (minus derived fields). Persists;
  returns the config **masked** (password shown as set/not-set only).
- `POST /api/projects/{id}/auth/verify` — runs `capture_login_session`; returns
  `{ ok, screenshot_b64, error }` and updates `verified_at` / `last_error`.
- `GET /api/projects/{id}` — includes `auth_config` **masked**.
- `run-playwright` (existing) — internally resolves the project's session file and passes
  `storage_state_path` to the runner. No client-facing change.

## UI

Project Overview gains a **"Login setup"** card (modeled on the existing Base URL card):

- Login URL, Username, Password (masked input), collapsible **Advanced** section with the
  three optional selector fields and an optional success-check field.
- **Test login & save session** button → calls `verify`, shows a spinner, then a success
  screenshot thumbnail or the error.
- Status line: `Not set` / `Session saved · verified <relative time>` / `Last attempt
  failed: <reason>`.
- Reuses the existing edit/preview pattern and masked-secret conventions from the
  Settings key UI.

## Error handling

- Missing/invalid login URL → 400 with a clear message (reuse `_validate_url`).
- Login fails (wrong creds / selectors / no navigation) → `verify` returns
  `{ ok: false, error, screenshot_b64 }`; the screenshot helps the user debug; nothing is
  saved.
- Session file missing at run time but `auth_config` present → attempt a capture first;
  if that fails, run unauthenticated and surface a note.
- Auto-relogin retry is capped at one attempt to avoid loops.
- `capture_login_session` runs in the same isolation as test runs (separate subprocess,
  scrubbed env, 60s timeout). The user-code **denylist does not apply** to it — the login
  script is trusted server-assembled code, not untrusted user/LLM input. The denylist
  continues to guard only the generated test code passed to `run_playwright_code`.

## Testing

- **Unit:** auth_config save/mask round-trip (password never returned in full);
  storage_state path resolution; `run_playwright_code` passes `storage_state` to the
  context when the file exists (mock the subprocess boundary).
- **Integration:** serve a tiny local HTML login form + a protected page (a fixture
  server), run `capture_login_session`, assert (a) the state file is written and (b) a
  subsequent `run_playwright_code` against the protected page sees the authenticated
  state. Add a negative case (wrong password → `ok: false`, no file written).
- Follows existing `tests/` patterns (`pytest.ini` sets `asyncio_mode = auto`).

## Security summary

- Credentials: SQLite only, masked, never in git / env / LLM prompts / generated code.
- Session file: gitignored `data/auth/`.
- Login script: server-assembled, ephemeral, sandboxed; credentials injected only there.
- Existing test-code denylist + env-scrub unchanged.
- Known gap (documented): credentials not encrypted at rest, matching current API-key
  storage.
