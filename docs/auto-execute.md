# Auto-execute test cases (Playwright)

The **Auto-execute** button on any test case detail turns it into a runnable Playwright test:

1. Set a **Base URL** in Project Overview (e.g. `https://your-app.example.com`).
2. Open a test case detail and click **Auto-execute**.
3. An LLM generates Playwright Python code for the case (shown with a loading spinner). The generated code is **stored on the test case and reused** on subsequent opens — click **Regenerate** to force a fresh generation. You can review and edit the code, and **Save code** to persist your edits.
4. Toggle **Watch it run** for a visible (headed) Chromium window — useful for live demos and debugging. Leave it off (headless) for normal runs.
5. Click **Run** — the server spawns a Chromium subprocess against your Base URL, executes the test, and returns pass / fail / error with a screenshot and console output. The code that was run is saved too.

The test case row in the feature accordion gets a small status dot (green / red / amber) reflecting the most recent run.

## When a test fails because the wording diverged

If the app's actual behavior is correct but the test's `expected_result` wording is stale, click **✓ Mark as expected behavior** in the result panel. This opens a small modal:

- The **original expected result** (read-only) and the **page text we observed** are shown side-by-side for reference.
- An LLM proposes a rewritten `expected_result` that documents the observed behavior. Click **🔄 Try another phrasing** for a different draft, or edit the textarea directly.
- Optionally tick **Regenerate Playwright code from new expected** if you want fresh code; leave it unchecked to keep code you've already edited by hand.
- Click **Save (replaces expected result)** — the test case is patched and the test re-runs immediately. If the re-run still fails, a small note appears suggesting that the test logic itself may need a manual edit.

The button only appears for **FAILED** results — it's hidden on `passed` (no need) and `error` (runner-level crash, not an assertion divergence).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/projects/{pid}/test-cases/{tcid}/generate-playwright` | Body `{ regenerate?: bool }`. Returns stored code (`cached: true`) unless `regenerate` is set; otherwise LLM-generates, stores, and returns `{ code, cached: false }`. |
| POST | `/api/projects/{pid}/test-cases/{tcid}/save-playwright` | Body `{ code }`. Persists hand-edited code. |
| POST | `/api/projects/{pid}/test-cases/{tcid}/run-playwright` | Body `{ code, headless }`. Returns `{ status, screenshot_b64, error_message, console_log, duration_ms }`. Persists the result and the run code on the test case row. |
| POST | `/api/projects/{pid}/test-cases/{tcid}/suggest-expected-result` | Body `{ actual_page_text, current_expected_result, error_message }`. LLM-rewrites the expected result based on observed behavior. Returns `{ suggested }`. Does NOT persist — use the existing PATCH endpoint to save. |

## Security boundaries

- Generated code is checked against a regex denylist (`subprocess`, `os.system`, `eval`, `exec`, `__import__`, `open(`, `urllib`, etc.) at both generate-time and run-time.
- The subprocess has a 60-second wall-clock timeout and is killed on overrun.
- The subprocess inherits the parent environment minus any var whose name contains `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `JWT`, or `PRIVATE_KEY` — so the user's stored LLM keys can't leak into generated test code.

## Authenticated runs (login)

Set up login once per project in **Project Overview → Login setup**: login URL,
username, password, and (optionally) selector overrides and a success check. Click
**Test login & save session** — the app logs in inside the sandbox and saves the
session (`data/auth/<project_id>.json`, gitignored). Every auto-execute run then starts
authenticated; if a run detects an expired session it re-logs-in once and retries.

**App home path:** authenticated tests start on the **App home path** (configured in Login setup, or auto-derived from a path-style Success check) instead of `/`. This ensures tests land on the correct authenticated page, not the bare domain root.

**Login-flow tests:** tests labeled as "login tests" (auto-detected by title pattern, or forced via the **Login test** toggle on the test case) run in a logged-out state and receive the saved username and password injected at run time — credentials are never stored in the generated code. This allows testing the login flow itself without hardcoding secrets.

Credentials are stored in SQLite (masked in the UI, never in git, never in generated
code). v1 supports a single login form (no MFA/SSO/OAuth) and Chromium only.

## Requirements

- `playwright install chromium` on the host (the Playwright Python package is already in `requirements.txt`; the Chromium binary is a separate one-time install).
