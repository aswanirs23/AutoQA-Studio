# browsermcp.io Driver + Optional Goal/Feature Derivation — Design

**Status:** Proposed
**Date:** 2026-05-07
**Author:** aswani12 (with Claude)

## Problem

The AI browser explorer (`POST /api/browser-session/{id}/explore`) currently uses
the Playwright driver, which launches a fresh, anonymous Chromium with no
cookies. If the user gives a URL gated behind authentication, Chromium lands on
the login page; with no credentials available, the explorer either types junk
into the login form or stops with `stop_reason="blocked"`. The resulting test
cases describe the login page, not the feature the user wanted tested.

Additionally, the explore API requires both a `feature_name` (on session
creation) and a free-text `goal` (on explore start). Users who only want
"generate test cases for this page" must invent both fields up front, even
though much of that information is derivable from the page itself.

## Goals

1. Let the explorer drive the user's already-authenticated Chrome tab via
   browsermcp.io's MCP server, so post-login URLs work without credentials
   appearing in our system.
2. Make `feature_name` and `goal` optional. When omitted, derive them from the
   first snapshot of the navigated page so the user only needs to provide a
   URL.

## Non-goals

- Implementing a generic credential store, auto-login flows, or `storage_state`
  injection. Authentication is delegated to the user's real Chrome session via
  the browsermcp.io extension.
- Supporting Microsoft's Playwright MCP. We can add a second MCP driver later
  using the same `BrowserDriver` interface; out of scope here.
- Changing the manual recording flow (which already works for authenticated
  sites via the Cursor agent's MCP integration).
- Frontend UI changes. The Browser Session UI already exposes URL, feature
  name, goal, and driver fields.

## Background

- `BrowserDriver` protocol: see
  [backend/services/browser_explorer/drivers/__init__.py](../../../backend/services/browser_explorer/drivers/__init__.py)
  — methods are `start`, `navigate`, `snapshot`, `click`, `type`, `screenshot`,
  `current_url`, `page_title`, `close`.
- `Snapshot` shape: dict with `url`, `title`, `elements: [{ref, role, name,
  tag, testid, text, disabled, type}]`, `text_dump`, `summary`.
- `PlaywrightDriver` reference implementation injects JS to mark elements with
  `data-tcg-ref`, returning rich per-element data —
  [backend/services/browser_explorer/drivers/playwright_driver.py](../../../backend/services/browser_explorer/drivers/playwright_driver.py).
- `BrowserMcpDriver` exists as a stub raising `NotImplementedError` —
  [backend/services/browser_explorer/drivers/mcp_driver.py](../../../backend/services/browser_explorer/drivers/mcp_driver.py).
- The route at
  [backend/routers/browser_session.py](../../../backend/routers/browser_session.py)
  rejects empty goals at line 204; `feature_name` is required by
  `StartSessionBody`.

## Architecture

### High-level flow (driver=mcp, no goal/feature_name)

```
POST /api/browser-session/start (project_id, url)
  → bs_service.create_session(...) — feature_name=""
POST /api/browser-session/{id}/explore (driver: "mcp", no goal)
  → run_exploration(...)
     → _build_driver("mcp", settings) — constructs BrowserMcpDriver
     → Orchestrator.run()
        → driver.start() — spawns @browsermcp/mcp subprocess, MCP handshake
        → driver.navigate(url) — extension drives the connected Chrome tab
        → driver.snapshot() — first snapshot
        → if goal == "": derived = _derive_goal_from_snapshot(snap)
        → if feature_name == "": derived = _derive_feature_name(snap, sid)
        → bs_service.set_metadata(...) — persists derived fields + goal_source
        → tool loop runs as today, using derived goal in the system prompt
     → driver.close() — closes MCP session, terminates subprocess
```

### Component boundaries

- **`BrowserMcpDriver`** — implements `BrowserDriver`. All MCP plumbing
  (subprocess spawn, JSON-RPC, tool calls) lives inside the class. Outside it,
  no one knows MCP exists.
- **Goal/feature derivation** — pure functions in `orchestrator.py`. Called
  with the first snapshot. Independent of which driver produced the snapshot.
- **Settings → driver** — `_build_driver()` reads `browser_mcp_command` /
  `browser_mcp_args` from `Settings` and passes them to the constructor. The
  driver does not import `Settings`.

## Components

### 1. `BrowserMcpDriver` (replace stub)

**File:** `backend/services/browser_explorer/drivers/mcp_driver.py`

**Construction:**

```python
class BrowserMcpDriver:
    def __init__(
        self,
        *,
        mcp_command: str = "npx",
        mcp_args: list[str] | None = None,
        startup_timeout_seconds: float = 30.0,
        tool_timeout_seconds: float = 30.0,
    ): ...
```

**Lifecycle:**

- `start()`:
  1. Build `StdioServerParameters(command=mcp_command, args=mcp_args)`.
  2. Open `stdio_client(...)` and `ClientSession` via an `AsyncExitStack`.
  3. Call `session.initialize()` under `asyncio.wait_for(timeout=startup_timeout_seconds)`.
  4. Call `session.list_tools()`. Assert `{browser_navigate, browser_snapshot,
     browser_click, browser_type}` are all present, else raise
     `RuntimeError("browsermcp.io MCP server is missing required tools: ...")`
     so callers see `stop_reason="mcp_version_mismatch"`.
- `close()`:
  1. `await self._exit_stack.aclose()` — closes session, terminates subprocess
     via SIGTERM.
  2. Idempotent and tolerant of `ProcessLookupError` (subprocess may already
     be dead from a crash).

**Tool method mapping:**

| `BrowserDriver` method | MCP tool | Args |
|---|---|---|
| `navigate(url)` | `browser_navigate` | `{url}` |
| `snapshot()` | `browser_snapshot` | (no args) |
| `click(ref)` | `browser_click` | `{ref, element: <cached name>}` |
| `type(ref, value)` | `browser_type` | `{ref, element: <cached name>, text: value, submit: false}` |
| `screenshot(path)` | `browser_screenshot` | (no args); decode base64 → write to `path` |
| `current_url()` | (cached) | from last snapshot's URL line |
| `page_title()` | (cached) | from last snapshot's title line |

Cached names are kept in `self._element_names: dict[str, str]` (ref → name)
populated during `snapshot()`. `click`/`type` look up the human-readable name
to send as the MCP `element` arg, which browsermcp uses for logging.

Each tool call wraps `session.call_tool(name, args)` in
`asyncio.wait_for(..., timeout=tool_timeout_seconds)`.

**Snapshot translation — `_parse_browsermcp_snapshot(text: str) -> Snapshot`:**

Input — browsermcp's accessibility-tree text, roughly:

```yaml
- Page URL: https://example.com/dashboard
- Page Title: Dashboard
- generic [ref=e1]:
  - heading "Filters" [ref=e2]
  - textbox "Search" [ref=e3]
  - button "Apply" [ref=e4]
  - button "Reset" [ref=e5] [disabled]
```

Output — a `Snapshot` with:

- `url` from the `Page URL:` line.
- `title` from the `Page Title:` line.
- `elements` — one per line containing `[ref=eN]`. Per element:
  - `ref` — verbatim from the `[ref=eN]` token.
  - `role` — first whitespace-delimited token.
  - `name` — quoted string (if present), else `""`.
  - `disabled` — `True` if `[disabled]` token present.
  - `tag`, `testid`, `text`, `type` — empty string / `None`. **Documented gap:
    browsermcp.io does not expose `browser_evaluate`, so per-element HTML
    metadata is unavailable. The orchestrator and ledger only require
    `ref/role/name`.**
- `text_dump` — joined lines of `role|name|testid|disabled` (matches Playwright
  driver's format so downstream consumers see the same shape).
- `summary` — first 8 headings + first 60 elements (matches Playwright driver).

**Error mapping:**

| Failure | Detection | Raised |
|---|---|---|
| `npx` / Node not on PATH | subprocess spawn fails | `FileNotFoundError` (caught as `mcp_unavailable`) |
| Install of `@browsermcp/mcp` fails | startup timeout / subprocess exits non-zero | `TimeoutError` or `RuntimeError` (caught as `mcp_unavailable`) |
| Required tools missing | `list_tools()` post-check | `RuntimeError` (caught as `mcp_version_mismatch`) |
| No Chrome tab connected | tool error message matches `("not connected"\|"no tab"\|"connect.*extension")` (case-insensitive) | `BrowserNotConnectedError` (new exception, sibling of `RefNotFoundError`) |
| Stale ref | tool error message matches `("ref.*not found"\|"unknown ref"\|"stale")` | `RefNotFoundError` (existing) |
| Subprocess dies mid-run | `call_tool` raises | `RuntimeError("browsermcp server crashed: <stderr tail>")` (caught as `mcp_crashed`) |

The substring matching is heuristic; a comment in the code notes that if
upstream changes their copy, update the match list.

### 2. New exception: `BrowserNotConnectedError`

**File:** `backend/services/browser_explorer/drivers/__init__.py`

Sibling of `RefNotFoundError`. Subclass of `RuntimeError`. The orchestrator
catches it before the generic exception handler and stops with
`stop_reason="not_connected"` and a user-facing message instructing them to
click "Connect" on the browsermcp Chrome extension.

### 3. `_build_driver` — pass settings through

**File:** `backend/services/browser_explorer/__init__.py`

```python
def _build_driver(name: str, *, headless: bool, host_allowlist: list[str] | None,
                  settings: Settings) -> BrowserDriver:
    n = (name or "playwright").lower()
    if n == "playwright":
        return PlaywrightDriver(headless=headless, host_allowlist=host_allowlist)
    if n == "mcp":
        return BrowserMcpDriver(
            mcp_command=settings.browser_mcp_command,
            mcp_args=(settings.browser_mcp_args or "").split(),
            startup_timeout_seconds=float(settings.browser_mcp_startup_timeout_seconds),
            tool_timeout_seconds=float(settings.browser_mcp_tool_timeout_seconds),
        )
    raise ValueError(f"unknown driver {name!r}; valid: playwright, mcp")
```

Caller (`_run_exploration_impl`) updated to pass `settings=s`.

### 4. New settings

**File:** `backend/config.py`

```python
# --- Browser MCP driver (browsermcp.io) ---
browser_mcp_command: str = "npx"
browser_mcp_args: str = "-y @browsermcp/mcp@latest"
browser_mcp_startup_timeout_seconds: int = 30
browser_mcp_tool_timeout_seconds: int = 30
```

Stored as a space-separated string for `args`, split on whitespace at driver
construction time. Avoids the SECRET_OVERRIDABLE_KEYS / Settings UI changes
since these are operational knobs, not secrets.

### 5. Optional `feature_name` and `goal`

**File:** `backend/models/browser_session.py`

```python
class StartSessionBody(BaseModel):
    project_id: str
    url: str
    feature_name: str = ""        # was required (no default)
    browser_type: str = "playwright"
    steps: list[str] | None = None
```

**File:** `backend/routers/browser_session.py`

```python
class ExploreStartBody(BaseModel):
    goal: str = ""                # was required (no default)
    # ... rest unchanged

@router.post("/{session_id}/explore", ...)
async def start_exploration(...):
    # Remove the early `if not body.goal.strip(): raise HTTPException(400)`
    # The orchestrator will derive a goal if empty.
    ...
```

### 6. Goal & feature derivation

**File:** `backend/services/browser_explorer/orchestrator.py`

Two pure functions:

```python
def _derive_goal_from_snapshot(snap: Snapshot) -> str:
    title = (snap.get("title") or "").strip()
    elements = snap.get("elements") or []
    forms = sum(1 for el in elements
                if el.get("role") in ("textbox", "combobox", "checkbox", "radio"))
    buttons = [el.get("name") or "" for el in elements
               if el.get("role") == "button" and el.get("name")]
    n_buttons = len(buttons)
    first_buttons = ", ".join(f"'{b}'" for b in buttons[:3])

    if n_buttons == 0 and forms == 0:
        return ("Explore this page and document all features, forms, "
                "validations, and errors you encounter.")

    pieces = []
    if forms:
        pieces.append(f"verify the {forms} input field(s) including "
                      "validation and error handling")
    if n_buttons:
        clause = f"exercise the {n_buttons} primary action(s)"
        if first_buttons:
            clause += f" ({first_buttons})"
        pieces.append(clause)

    page_clause = f"On the page titled '{title}': " if title else "On this page: "
    return page_clause + " and ".join(pieces) + "."


def _derive_feature_name(snap: Snapshot, session_id: str) -> str:
    title = (snap.get("title") or "").strip()
    if title:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        if slug:
            return slug[:60]
    return f"browser_session_{session_id[-8:]}"
```

**Note:** `Snapshot` is a `dict` subclass (see
[drivers/__init__.py](../../../backend/services/browser_explorer/drivers/__init__.py));
elements are plain dicts. Use `.get()` access, not attribute access.

Called from `Orchestrator.run()` immediately after the first snapshot, before
the LLM tool loop starts. After derivation, the orchestrator persists:

- The derived `goal` and `goal_source` ("auto" or "user") via
  `bs_service.set_metadata(...)` (existing helper writes to `metadata_json`).
- The derived `feature_name` via a **new** `bs_service.update_feature_name(db,
  session_id, name)` helper that writes to the `feature_name` column. Only
  called when the original session was created with empty `feature_name`.

The new helper is small (~10 lines, mirrors `complete_session`'s shape):

```python
async def update_feature_name(
    db: aiosqlite.Connection, session_id: str, feature_name: str,
) -> BrowserSession | None:
    session = await get_session(db, session_id)
    if not session:
        return None
    await db.execute(
        "UPDATE browser_sessions SET feature_name = ? WHERE id = ?",
        (feature_name, session_id),
    )
    return session.model_copy(update={"feature_name": feature_name})
```

The Evidence Ledger prompt at
[browser_session_parser.py:88](../../../backend/services/parsers/browser_session_parser.py)
then sees the derived goal verbatim, and `feature_name` is read from the
column at line 200.

If the first snapshot fails (driver raises before returning), the orchestrator
stops immediately with `stop_reason="not_connected"` (or `"navigate_failed"`).
The loop never runs.

## Data flow & state changes

`BrowserSession.metadata` gets two new keys after derivation:

```json
{
  "mode": "ai_explore",
  "goal": "<derived or user-supplied>",
  "goal_source": "auto" | "user",
  "evidence_ledger": { ... },
  "tool_loop_result": { ... }
}
```

`goal_source` is purely informational — debugging aid for "why did the
explorer focus on X instead of Y".

`BrowserSession.feature_name` (column) is updated post-derivation when the
session was created with empty feature_name. Existing rows with
`feature_name != ""` are untouched.

## Dependencies

- `mcp>=1.0` added to `requirements.txt`. Provides `stdio_client`,
  `ClientSession`, `StdioServerParameters`. Anthropic-maintained reference
  implementation of the MCP protocol.
- Runtime: Node.js / `npx` on the host running the backend. Documented in the
  user-facing error message when missing.
- User-side: browsermcp.io Chrome extension installed and "Connect" clicked on
  some tab. Documented in the not-connected error message.

## Testing strategy

**Layer 1 — Unit tests (CI, deterministic):**

- `_parse_browsermcp_snapshot`: ~10 fixtures covering empty page, simple form,
  disabled buttons, nested groups, malformed input, exotic refs.
- `_derive_goal_from_snapshot`: title only, buttons only, form+buttons,
  neither (→ A fallback), Unicode title.
- `_derive_feature_name`: empty title, all-symbols title, very long title
  (60-char cap), Unicode.
- Error-message → exception mapping: pass canned `@browsermcp/mcp` error
  strings, assert correct exception type.
- Settings: `browser_mcp_args` whitespace-splits cleanly.

**Layer 2 — Driver tests with mocked MCP `ClientSession`:**

- `unittest.mock.AsyncMock` for `ClientSession`. Stub `call_tool(name, args)`
  to return canned responses.
- Verify `navigate`, `snapshot`, `click`, `type`, `screenshot` issue the
  correct tool calls with the correct args.
- Verify base64 screenshot decoding writes a valid PNG.
- Verify "not connected" error mapping → `BrowserNotConnectedError`.

**Layer 3 — One opt-in live integration test:**

- `@pytest.mark.skipif(not os.getenv("BROWSERMCP_LIVE"))` — manual run only.
- Spawns real `npx @browsermcp/mcp`, expects extension already connected.
- Smoke-tests `start → navigate → snapshot → close`.

**Manual verification checklist (added to PR description):**

1. Install browsermcp.io Chrome extension; click Connect on a logged-in tab.
2. `POST /api/browser-session/start` with just `url` (no `feature_name`).
3. `POST /{id}/explore` with `driver: "mcp"`, no `goal`.
4. Poll `/explore/status`: `actions_count` climbs; `metadata.goal`
   populated with derived text.
5. Confirm `metadata.goal_source == "auto"`,
   `feature_name` matches title slug.
6. Run generate from the session — confirm test cases reference real elements
   from the authenticated page.

**Out of testing scope:**

- Chrome extension itself (third-party).
- `@browsermcp/mcp` server's own behavior (third-party).
- End-to-end route → ledger → test cases (covered by existing browser-session
  tests with a fake driver).

## Risks & open questions

- **browsermcp.io snapshot format drift.** Our line parser is coupled to the
  YAML-ish output. Mitigation: ~10 fixture tests; if upstream changes the
  format we'll see test failures and a clear PR signal.
- **Error-string heuristics for "not connected" / stale ref.** Same drift
  risk. Comment in code lists the matched substrings; bumping the
  `@browsermcp/mcp` version will require revisiting.
- **Less-detailed test cases when using MCP driver.** No `data-testid` /
  `tag` / `type` per element. Acceptable trade-off — auth access matters more
  than testid specificity. Documented in the spec; consider Playwright MCP
  later if this becomes a real pain.
- **Cold-start latency.** First `npx @browsermcp/mcp@latest` invocation
  downloads the package (~MB). The 30-second startup timeout absorbs this; if
  it's still painful in practice, users can pre-install
  (`npm i -g @browsermcp/mcp`) and adjust `browser_mcp_command`.
- **Frontend assumed sufficient.** The frontend at
  [frontend/app.js](../../../frontend/app.js) already exposes the driver
  dropdown (`ai_driver`, line 1056) and a feature_name field; empty values
  flow through to the API transparently. No frontend changes planned. If
  client-side validation rejects empty feature_name, that's a one-line
  follow-up.

## Rollout

- Default `browser_explorer_default_driver` stays `"playwright"`. Users opt
  into MCP per-call via the `driver: "mcp"` parameter on `/explore`. No
  behavioral change for existing users.
- After confidence, consider a settings toggle to flip the default. Out of
  scope here.
