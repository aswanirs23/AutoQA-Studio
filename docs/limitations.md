# Known limitations & roadmap

An honest inventory of what AutoQA Studio does **not** do well yet, grounded in how
the code actually works, plus concrete proposals for the highest-impact fixes.

## Input & context

- **Jira: single issue, text only.** [`jira_parser.py`](../backend/services/parsers/jira_parser.py)
  fetches one issue's summary + description (ADF → text) + labels. With
  `include_linked` it adds subtasks/linked issues, but only their **key + summary**.
  It does **not** fetch Confluence pages, follow links into related docs, or download
  **attachments / embedded images** in the PRD.
- **No knowledge of the real application.** Generation sees only the merged
  `ParsedInput` + the project description. It has no map of your app's pages, routes,
  or screens — so "related pages" are known only if they're written into the PRD text
  or project description. No crawling, no RAG/embeddings.
- **Jira Cloud only.** REST v3 / ADF; Jira Server / Data Center (wiki markup) is
  unsupported. Auth is basic email + API token (no OAuth).
- **Shallow Figma / single-image screenshots.** Figma pulls structure + text but
  misses interactions and visual nuance; the screenshot parser is one image at a time
  (no multi-screen flow understanding).
- **Large PRDs** can exceed the model context window; there is no chunking/summarization.

## Test-case generation

- **Exact-match dedup only.** [`dedup_service.py`](../backend/services/dedup_service.py)
  hashes `title + steps` with SHA-256, so reworded or semantically-duplicate cases slip
  through.
- **Non-deterministic.** Regenerating produces different cases; there is no
  generation history or diff.
- **No coverage analysis.** No requirement → test-case coverage matrix or gap report.

## Auto-execute (Playwright)

- **Selectors are guessed blind.** [`generate_playwright_code`](../backend/services/llm_service.py)
  receives only `{title, preconditions, steps, expected_result}` + `base_url` — no live
  DOM. It infers selectors from the test text, so it fails when the real page differs.
  **Navigation path** is now taken from the configured landing page (set in Login setup or
  derived from a path-style Success check), but selectors are still guessed. The "mark as
  expected" flow fixes wording drift, not structural mismatches. DOM-aware generation
  remains the open fix (#2).
- **Login / auth is now supported via project Login setup.** Set up credentials in
  Project Overview; the runner saves session state and reuses it across runs, so every
  auto-execute starts already authenticated. If a session expires during a run, the
  runner re-logs-in once and retries. Credentials are stored in SQLite (unencrypted,
  masked in the UI, never in git or generated code). v1 supports a single login form
  (no MFA/SSO/OAuth) and Chromium only.
- **Every test is independent.** No shared setup/teardown, page objects, or ordering —
  a multi-page flow must re-navigate (and re-login) from `base_url` every time.
- **Operational limits.** 60-second hard timeout; Chromium only (no Firefox/WebKit);
  headed "watch it run" won't work on a headless server; the denylist blocks `open(`,
  `requests`, `urllib`, so tests can't upload files, read fixtures, or call an API to
  set up state.
- **Not a real sandbox.** The regex denylist is defense-in-depth, not isolation, and is
  bypassable via obfuscation. Running LLM-generated code carries inherent risk.
- **No CI story.** Tests run one at a time inside the app; there is no "run all",
  regression suite, parallelism, scheduling, or export to a runnable pytest suite.
  Reporting is pass/fail + one screenshot + console (no traces/videos/step results).

## Platform

- **Single-tenant by default.** `AUTH_DISABLED=true` → one built-in local user; no real
  multi-tenancy, RBAC, sharing, or collaboration (comments, review, assignment).
- **SQLite + one worker.** No horizontal scaling; concurrent writes risk corruption
  (migrate to Postgres before scaling). Screenshots are stored base64 in the DB
  (bloat), and test-case lists are unpaginated (slow for large projects).
- **No rate limiting** on generate/run endpoints (cost & abuse exposure).
- **One-way integrations.** Export to TestRail/CSV works, but run results are not pushed
  back to Jira/TestRail. No import of existing test cases; no test versioning; no i18n.

---

## Proposed fixes (highest impact first)

### ✅ 1. Authenticated auto-execute via reusable `storage_state` (shipped)

**Problem:** tests behind login can't run, and credentials can't be passed safely.

**Approach — capture once, reuse everywhere (no credentials in test code):**

1. Add per-project auth config (new `project_auth` table or a `project.context` key):
   login URL + a small login recipe (field selectors) **or** just credentials, stored
   server-side.
2. Add a **"Set up login"** action that runs the login flow once in the runner and saves
   Playwright's `storage_state` (cookies + localStorage) to disk per project
   (`data/auth/<project_id>.json`), never returned to the client.
3. In [`playwright_runner.py`](../backend/services/playwright_runner.py), have the wrapper
   create the context with that state:
   `context = await browser.new_context(storage_state=state_path)` when present.
4. Every generated test then starts **already authenticated** — no login steps, no
   secrets in code, no env-scrub conflict.
5. Refresh the saved state on demand (or when a run fails with an auth redirect).

This also unblocks proposal #2 for post-login pages.

### 2. DOM-snapshot-aware code generation

**Problem:** selectors/paths are guessed from text, causing flaky false failures.

**Approach — ground the model in the real page:**

1. Before generating, navigate to the inferred path (authenticated via #1) and capture a
   **compact accessibility/DOM snapshot** — interactive elements with their roles,
   accessible names, and placeholders (e.g. `page.accessibility.snapshot()` trimmed to
   inputs/buttons/links).
2. Pass that snapshot into `build_playwright_user_message` so the LLM selects **real**
   locators instead of guessing.
3. Add a **self-healing loop**: on a run failure, capture the actual page snapshot and
   regenerate the code once with that context before reporting failure.

### 3. Richer Jira / PRD ingestion

- Fetch issue **attachments** and run images through the existing vision path.
- Optionally add a **Confluence** parser (PRDs often live there) and follow issue links
  to pull linked-issue **descriptions**, not just summaries.
- Support Jira Server/Data Center (v2 API / wiki markup).

### 4. Export a runnable test suite

- Generate a standalone `pytest` + Playwright project (with the auth fixture from #1) so
  suites can run in CI, in parallel, cross-browser — decoupling execution from the app.

### 5. Semantic dedup & coverage

- Replace exact-hash dedup with embedding similarity to catch reworded duplicates.
- Add a requirement → test-case coverage view to surface gaps.
