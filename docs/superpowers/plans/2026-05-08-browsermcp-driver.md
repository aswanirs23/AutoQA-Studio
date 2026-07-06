# browsermcp.io Driver + Optional Goal/Feature Derivation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a real `BrowserMcpDriver` against browsermcp.io's MCP server so the AI explorer can drive the user's authenticated Chrome tab; make `feature_name` and `goal` optional with snapshot-derived defaults.

**Architecture:** New driver speaks MCP-over-stdio to a spawned `npx @browsermcp/mcp` subprocess; same `BrowserDriver` Protocol shape as `PlaywrightDriver` so it's a drop-in. Goal/feature derivation lives in the orchestrator, called once after the first snapshot. Per-call opt-in via `driver: "mcp"`; default driver stays `playwright`.

**Tech Stack:** Python 3.13+, FastAPI, `mcp>=1.0` (Anthropic MCP SDK), Pydantic, aiosqlite, pytest + pytest-asyncio (newly bootstrapped).

**Spec:** [docs/superpowers/specs/2026-05-07-browsermcp-driver-design.md](../specs/2026-05-07-browsermcp-driver-design.md)

**Project rule reminder:** This project requires user approval before every `git commit`. Each task ends with a "Commit" step — **pause and request approval before running the commit command**, then proceed once granted. Do not commit silently.

---

## File Structure

**New files:**
- `tests/__init__.py` — marker
- `tests/conftest.py` — pytest config + shared fixtures
- `tests/browser_explorer/__init__.py` — marker
- `tests/browser_explorer/test_snapshot_parser.py` — `_parse_browsermcp_snapshot` tests
- `tests/browser_explorer/test_derivation.py` — goal + feature_name derivation tests
- `tests/browser_explorer/test_mcp_driver.py` — driver tests with mocked `ClientSession`
- `tests/browser_explorer/test_mcp_driver_live.py` — opt-in smoke test (gated by `BROWSERMCP_LIVE` env var)
- `tests/services/__init__.py` — marker
- `tests/services/test_browser_session_service.py` — `update_feature_name` helper test
- `pytest.ini` — pytest config

**Modified files:**
- `requirements.txt` — add `mcp>=1.0`, `pytest>=8.0`, `pytest-asyncio>=0.24`
- `backend/config.py` — add 4 new MCP-related Settings fields
- `backend/services/browser_explorer/drivers/__init__.py` — add `BrowserNotConnectedError`
- `backend/services/browser_explorer/drivers/mcp_driver.py` — replace stub with full implementation
- `backend/services/browser_explorer/__init__.py` — pass settings into `_build_driver`
- `backend/services/browser_session.py` — add `update_feature_name` helper
- `backend/models/browser_session.py` — `StartSessionBody.feature_name` default ""
- `backend/routers/browser_session.py` — `ExploreStartBody.goal` default ""; drop empty guard
- `backend/services/browser_explorer/orchestrator.py` — derivation + persistence after first snapshot

**File responsibility boundaries:**
- `mcp_driver.py` owns *all* browsermcp.io knowledge (subprocess, JSON-RPC, tool name mapping, snapshot parsing). Nothing else imports the `mcp` SDK directly.
- Derivation lives in `orchestrator.py` because it's snapshot-shaped logic that runs in the explore loop, not a route concern.
- Tests under `tests/` mirror the source tree under `backend/`.

---

## Task 1: Bootstrap pytest and test directory layout

**Files:**
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/browser_explorer/__init__.py`
- Create: `tests/services/__init__.py`
- Modify: `requirements.txt`

The project has no test infrastructure today. Establish it before any TDD work.

- [ ] **Step 1: Add pytest dependencies to `requirements.txt`**

Append to the end of `requirements.txt`:

```
# Testing
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 2: Install the new deps**

Run: `pip install -r requirements.txt`
Expected: `pytest` and `pytest-asyncio` installed; existing deps unchanged.

- [ ] **Step 3: Create `pytest.ini` at repo root**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
python_files = test_*.py
python_classes = Test*
python_functions = test_*
filterwarnings =
    ignore::DeprecationWarning
```

`asyncio_mode = auto` lets us write `async def test_x()` without per-test `@pytest.mark.asyncio` decorators.

- [ ] **Step 4: Create empty marker files**

```bash
touch tests/__init__.py tests/browser_explorer/__init__.py tests/services/__init__.py
```

- [ ] **Step 5: Create `tests/conftest.py` with a smoke test discovery fixture**

```python
"""Shared pytest fixtures for the Test Case Generator backend."""

import pytest


@pytest.fixture
def sample_session_id() -> str:
    """A deterministic session ID for tests that derive feature names from it."""
    return "bs_abc12345def6"
```

- [ ] **Step 6: Verify pytest discovers the empty test tree**

Run: `pytest --collect-only`
Expected: `collected 0 items` (no errors), prints discovered paths.

- [ ] **Step 7: Commit**

Request user approval, then:

```bash
git add requirements.txt pytest.ini tests/
git commit -m "test: bootstrap pytest with asyncio support"
```

---

## Task 2: Add new MCP-related settings

**Files:**
- Modify: `backend/config.py:64-71`
- Test: `tests/test_config.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_config.py`:

```python
"""Settings sanity checks for new browser MCP fields."""

from backend.config import Settings


def test_browser_mcp_defaults():
    s = Settings()
    assert s.browser_mcp_command == "npx"
    assert s.browser_mcp_args == "-y @browsermcp/mcp@latest"
    assert s.browser_mcp_startup_timeout_seconds == 30
    assert s.browser_mcp_tool_timeout_seconds == 30


def test_browser_mcp_args_split_on_whitespace():
    """Settings stores args as a string; consumers split on whitespace."""
    s = Settings(browser_mcp_args="--foo bar  --baz")
    assert s.browser_mcp_args.split() == ["--foo", "bar", "--baz"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'browser_mcp_command'`

- [ ] **Step 3: Add the four new fields to `backend/config.py`**

Locate the `# --- Browser explorer (AI-driven exploration → test cases) ---` block (around line 64) and append a new block immediately after it, before the closing of the `Settings` class:

```python
    # --- Browser MCP driver (browsermcp.io) ---
    # Spawned via stdio. Args are space-separated for easy override.
    # The user must install the browsermcp.io Chrome extension and click
    # "Connect" on a logged-in tab before kicking off an MCP-driven explore.
    browser_mcp_command: str = "npx"
    browser_mcp_args: str = "-y @browsermcp/mcp@latest"
    browser_mcp_startup_timeout_seconds: int = 30
    browser_mcp_tool_timeout_seconds: int = 30
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add backend/config.py tests/test_config.py
git commit -m "config: add browser MCP driver settings (command, args, timeouts)"
```

---

## Task 3: Add `BrowserNotConnectedError` exception

**Files:**
- Modify: `backend/services/browser_explorer/drivers/__init__.py:20-22`

Trivial addition; no separate test (covered in driver tests later).

- [ ] **Step 1: Add the exception class**

In `backend/services/browser_explorer/drivers/__init__.py`, immediately after the existing `RefNotFoundError`:

```python
class RefNotFoundError(Exception):
    """Raised when a tool tries to act on a ref that no longer exists."""


class BrowserNotConnectedError(RuntimeError):
    """Raised when an MCP-backed driver call hits the server but no Chrome
    tab is connected (user hasn't clicked "Connect" on the browsermcp.io
    extension, or the tab was closed mid-run).
    """
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from backend.services.browser_explorer.drivers import BrowserNotConnectedError; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

Request user approval, then:

```bash
git add backend/services/browser_explorer/drivers/__init__.py
git commit -m "drivers: add BrowserNotConnectedError exception"
```

---

## Task 4: Add `mcp` Python SDK to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append to `requirements.txt` under the browser-exploration block**

After the existing `playwright>=1.48.0` line:

```
mcp>=1.0.0
```

- [ ] **Step 2: Install it**

Run: `pip install -r requirements.txt`
Expected: `mcp` installs cleanly. If install fails (e.g. on older Python), report and stop here for triage.

- [ ] **Step 3: Smoke-check the imports we'll use**

Run:

```bash
python -c "from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client; print('ok')"
```

Expected: `ok`. If imports fail, the SDK's surface has changed — pause and investigate before continuing.

- [ ] **Step 4: Commit**

Request user approval, then:

```bash
git add requirements.txt
git commit -m "deps: add mcp>=1.0 for browsermcp.io driver"
```

---

## Task 5: TDD — `_parse_browsermcp_snapshot()` parser

**Files:**
- Create: `tests/browser_explorer/test_snapshot_parser.py`
- Will be implemented in: `backend/services/browser_explorer/drivers/mcp_driver.py` (next task wires it in; this task delivers the standalone function)

The parser converts browsermcp.io's accessibility-tree text into our `Snapshot` shape. It's the most failure-prone piece of the driver — fixture-test it thoroughly before integration.

- [ ] **Step 1: Write the failing tests**

Create `tests/browser_explorer/test_snapshot_parser.py`:

```python
"""Tests for the browsermcp.io snapshot text parser."""

import pytest

from backend.services.browser_explorer.drivers.mcp_driver import (
    _parse_browsermcp_snapshot,
)


SIMPLE_PAGE = """- Page URL: https://example.com/dashboard
- Page Title: Dashboard
- generic [ref=e1]:
  - heading "Filters" [ref=e2]
  - textbox "Search" [ref=e3]
  - button "Apply" [ref=e4]
  - button "Reset" [ref=e5] [disabled]"""


EMPTY_PAGE = """- Page URL: https://example.com/blank
- Page Title: """


NO_REFS = """- Page URL: https://example.com/static
- Page Title: Static
- generic:
  - paragraph "Just text, no interactive elements."
"""


WEIRD_REFS = """- Page URL: https://example.com/x
- Page Title: X
- button "Save" [ref=e123abc]
- link "Help" [ref=e0]"""


def test_parse_simple_page_extracts_url_and_title():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    assert snap["url"] == "https://example.com/dashboard"
    assert snap["title"] == "Dashboard"


def test_parse_simple_page_extracts_elements_with_refs():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    refs = [el["ref"] for el in snap["elements"]]
    # The "generic" container has a ref but no role we care about — we still
    # include it; the orchestrator filters by role later.
    assert "e2" in refs
    assert "e3" in refs
    assert "e4" in refs
    assert "e5" in refs


def test_parse_role_and_name():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    apply_btn = next(el for el in snap["elements"] if el["ref"] == "e4")
    assert apply_btn["role"] == "button"
    assert apply_btn["name"] == "Apply"


def test_parse_disabled_flag():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    reset_btn = next(el for el in snap["elements"] if el["ref"] == "e5")
    assert reset_btn["disabled"] is True

    apply_btn = next(el for el in snap["elements"] if el["ref"] == "e4")
    assert apply_btn["disabled"] is False


def test_parse_missing_fields_have_safe_defaults():
    """Per spec: tag, testid, text, type are unavailable from browsermcp.
    Snapshot shape consumers expect these keys to be present though."""
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    el = snap["elements"][0]
    assert el["tag"] == ""
    assert el["testid"] is None
    assert el["text"] == ""
    assert el["type"] is None


def test_parse_empty_page():
    snap = _parse_browsermcp_snapshot(EMPTY_PAGE)
    assert snap["url"] == "https://example.com/blank"
    assert snap["title"] == ""
    assert snap["elements"] == []


def test_parse_page_with_no_interactive_elements():
    snap = _parse_browsermcp_snapshot(NO_REFS)
    assert snap["elements"] == []


def test_parse_weird_ref_ids():
    snap = _parse_browsermcp_snapshot(WEIRD_REFS)
    refs = [el["ref"] for el in snap["elements"]]
    assert "e123abc" in refs
    assert "e0" in refs


def test_parse_includes_text_dump_and_summary():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    assert "text_dump" in snap and isinstance(snap["text_dump"], str)
    assert "summary" in snap and isinstance(snap["summary"], str)
    # text_dump should mention each interactive element so state-hash dedup works
    assert "Apply" in snap["text_dump"]


def test_parse_malformed_input_does_not_raise():
    """If browsermcp returns garbage, return an empty-but-valid snapshot
    rather than crashing the explore loop."""
    snap = _parse_browsermcp_snapshot("garbage with no recognizable structure")
    assert snap["url"] == ""
    assert snap["title"] == ""
    assert snap["elements"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/browser_explorer/test_snapshot_parser.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_browsermcp_snapshot'` (the symbol doesn't exist yet).

- [ ] **Step 3: Implement the parser in `mcp_driver.py`**

Replace the entire contents of `backend/services/browser_explorer/drivers/mcp_driver.py` with the **partial** implementation below. (Subsequent tasks will add the `BrowserMcpDriver` class itself; for now the parser is enough to make these tests pass.)

```python
"""Browser MCP driver — connects to browsermcp.io's MCP server.

The MCP server (`@browsermcp/mcp` npm package) is spawned as a stdio
subprocess. It bridges to the user's Chrome via the browsermcp.io extension,
which the user must install and click "Connect" on for a logged-in tab.

This module deliberately keeps all browsermcp-specific knowledge contained:
the parser below, the tool-name mapping, and error-string heuristics. The
orchestrator works against the BrowserDriver Protocol and never imports the
`mcp` SDK.
"""

from __future__ import annotations

import re

from backend.services.browser_explorer.drivers import Snapshot


# -------- Snapshot parser ---------------------------------------------------

_REF_TOKEN = re.compile(r"\[ref=([A-Za-z0-9_-]+)\]")
_QUOTED_NAME = re.compile(r'"([^"]*)"')
_PAGE_URL = re.compile(r"^-\s*Page URL:\s*(.*)$", re.MULTILINE)
_PAGE_TITLE = re.compile(r"^-\s*Page Title:\s*(.*)$", re.MULTILINE)


def _parse_browsermcp_snapshot(text: str) -> Snapshot:
    """Parse browsermcp.io's accessibility-tree text into a Snapshot.

    The output format browsermcp returns is YAML-ish, e.g.:

        - Page URL: https://example.com
        - Page Title: Example
        - button "Sign in" [ref=e3]

    We extract URL, title, and one element per line that contains a
    `[ref=...]` token. Per the spec, fields browsermcp does not expose
    (tag, testid, text, type) are filled with empty/None defaults so
    downstream consumers see a uniform Snapshot shape.

    Tolerant of malformed input: returns an empty-but-valid Snapshot rather
    than raising, so a single bad response can't crash the explore loop.
    """
    url_match = _PAGE_URL.search(text or "")
    title_match = _PAGE_TITLE.search(text or "")
    url = url_match.group(1).strip() if url_match else ""
    title = title_match.group(1).strip() if title_match else ""

    elements: list[dict] = []
    for line in (text or "").splitlines():
        ref_match = _REF_TOKEN.search(line)
        if not ref_match:
            continue
        ref = ref_match.group(1)

        # Strip leading dashes/whitespace, then take the first token as role.
        stripped = line.lstrip(" -")
        role_match = re.match(r"([a-zA-Z]+)", stripped)
        role = role_match.group(1).lower() if role_match else ""

        name_match = _QUOTED_NAME.search(stripped)
        name = name_match.group(1) if name_match else ""

        disabled = "[disabled]" in line

        elements.append({
            "ref": ref,
            "role": role,
            "name": name,
            "tag": "",
            "testid": None,
            "text": "",
            "disabled": disabled,
            "type": None,
        })

    text_dump_lines = [
        f"url:{url}",
        f"title:{title}",
        *[f"{el['role']}|{el['name']}|{el['testid'] or ''}|{'d' if el['disabled'] else ''}"
          for el in elements],
    ]
    summary_lines = [
        f"  [{el['ref']}] {el['role']}{' (disabled)' if el['disabled'] else ''} \"{el['name']}\""
        for el in elements[:60]
    ]

    return Snapshot({
        "url": url,
        "title": title,
        "elements": elements,
        "text_dump": "\n".join(text_dump_lines),
        "summary": "\n".join(summary_lines),
    })


# BrowserMcpDriver class added in the next task.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/browser_explorer/test_snapshot_parser.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_snapshot_parser.py backend/services/browser_explorer/drivers/mcp_driver.py
git commit -m "drivers/mcp: implement browsermcp snapshot parser with fixtures"
```

---

## Task 6: TDD — `_derive_goal_from_snapshot()` and `_derive_feature_name()`

**Files:**
- Create: `tests/browser_explorer/test_derivation.py`
- Modify: `backend/services/browser_explorer/orchestrator.py` (append helper functions)

- [ ] **Step 1: Write the failing tests**

Create `tests/browser_explorer/test_derivation.py`:

```python
"""Tests for goal + feature name derivation from the first snapshot."""

import pytest

from backend.services.browser_explorer.drivers import Snapshot
from backend.services.browser_explorer.orchestrator import (
    _derive_feature_name,
    _derive_goal_from_snapshot,
)


def _snap(title: str, elements: list[dict]) -> Snapshot:
    return Snapshot({
        "url": "https://example.com",
        "title": title,
        "elements": elements,
        "text_dump": "",
        "summary": "",
    })


def _btn(name: str) -> dict:
    return {"ref": "e1", "role": "button", "name": name, "disabled": False,
            "tag": "", "testid": None, "text": "", "type": None}


def _input() -> dict:
    return {"ref": "e2", "role": "textbox", "name": "Email", "disabled": False,
            "tag": "", "testid": None, "text": "", "type": None}


# ---- Goal derivation -------------------------------------------------------

def test_derive_goal_with_title_buttons_and_forms():
    snap = _snap("Dashboard Filters", [_input(), _btn("Apply"), _btn("Reset")])
    goal = _derive_goal_from_snapshot(snap)
    assert "Dashboard Filters" in goal
    assert "1 input field" in goal
    assert "2 primary action" in goal
    assert "'Apply'" in goal
    assert "'Reset'" in goal


def test_derive_goal_buttons_only():
    snap = _snap("Page", [_btn("Submit")])
    goal = _derive_goal_from_snapshot(snap)
    assert "1 primary action" in goal
    assert "input field" not in goal


def test_derive_goal_forms_only():
    snap = _snap("Form Page", [_input()])
    goal = _derive_goal_from_snapshot(snap)
    assert "1 input field" in goal
    assert "primary action" not in goal


def test_derive_goal_falls_back_to_generic_when_no_affordances():
    snap = _snap("Static Page", [])
    goal = _derive_goal_from_snapshot(snap)
    # Generic A-fallback wording from the spec
    assert "Explore this page and document" in goal


def test_derive_goal_handles_missing_title():
    snap = _snap("", [_btn("Go")])
    goal = _derive_goal_from_snapshot(snap)
    assert "On this page:" in goal
    assert "On the page titled" not in goal


def test_derive_goal_caps_button_examples_at_three():
    buttons = [_btn(f"Btn{i}") for i in range(10)]
    snap = _snap("Page", buttons)
    goal = _derive_goal_from_snapshot(snap)
    assert "10 primary action" in goal
    # Only the first 3 are quoted in the goal text
    assert goal.count("'") == 6  # 3 names × 2 quotes each


def test_derive_goal_unicode_title():
    snap = _snap("ダッシュボード", [_btn("適用")])
    goal = _derive_goal_from_snapshot(snap)
    assert "ダッシュボード" in goal
    assert "'適用'" in goal


# ---- Feature name derivation ----------------------------------------------

def test_derive_feature_name_from_title():
    snap = _snap("My Dashboard Page", [])
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert name == "my_dashboard_page"


def test_derive_feature_name_strips_punctuation():
    snap = _snap("Sign In - Acme Co.", [])
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert name == "sign_in_acme_co"


def test_derive_feature_name_caps_at_60_chars():
    snap = _snap("a " * 100, [])  # very long title
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert len(name) <= 60


def test_derive_feature_name_falls_back_when_title_empty(sample_session_id):
    snap = _snap("", [])
    name = _derive_feature_name(snap, sample_session_id)
    # Last 8 chars of the session ID
    assert name == f"browser_session_{sample_session_id[-8:]}"


def test_derive_feature_name_falls_back_when_title_only_punct(sample_session_id):
    snap = _snap("!!!", [])
    name = _derive_feature_name(snap, sample_session_id)
    assert name.startswith("browser_session_")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/browser_explorer/test_derivation.py -v`
Expected: FAIL — `ImportError: cannot import name '_derive_feature_name'`.

- [ ] **Step 3: Add the helpers to `orchestrator.py`**

In `backend/services/browser_explorer/orchestrator.py`, after the imports and before the `_DESTRUCTIVE_PATTERN` constant (around line 38), add:

```python
def _derive_goal_from_snapshot(snap: dict) -> str:
    """Build a focused exploration goal from the first snapshot.

    Used when the explore caller didn't provide a goal. Falls back to a
    generic "explore everything" goal when the page has no obvious
    affordances (no buttons, no input-like elements).
    """
    title = (snap.get("title") or "").strip()
    elements = snap.get("elements") or []
    forms = sum(
        1 for el in elements
        if el.get("role") in ("textbox", "combobox", "checkbox", "radio")
    )
    buttons = [
        el.get("name") or "" for el in elements
        if el.get("role") == "button" and el.get("name")
    ]
    n_buttons = len(buttons)

    if n_buttons == 0 and forms == 0:
        return ("Explore this page and document all features, forms, "
                "validations, and errors you encounter.")

    pieces: list[str] = []
    if forms:
        pieces.append(
            f"verify the {forms} input field(s) including validation and error handling"
        )
    if n_buttons:
        clause = f"exercise the {n_buttons} primary action(s)"
        first_three = ", ".join(f"'{b}'" for b in buttons[:3])
        if first_three:
            clause += f" ({first_three})"
        pieces.append(clause)

    page_clause = f"On the page titled '{title}': " if title else "On this page: "
    return page_clause + " and ".join(pieces) + "."


def _derive_feature_name(snap: dict, session_id: str) -> str:
    """Slugify the page title for use as a feature name; fall back to a
    deterministic id-based name if the title is empty or punctuation-only.
    """
    title = (snap.get("title") or "").strip()
    if title:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        if slug:
            return slug[:60]
    return f"browser_session_{session_id[-8:]}"
```

`re` is already imported at the top of `orchestrator.py` (line 25), so no new import is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/browser_explorer/test_derivation.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_derivation.py backend/services/browser_explorer/orchestrator.py
git commit -m "orchestrator: add goal + feature_name derivation from first snapshot"
```

---

## Task 7: TDD — `bs_service.update_feature_name()` helper

**Files:**
- Create: `tests/services/test_browser_session_service.py`
- Modify: `backend/services/browser_session.py` (append helper)

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_browser_session_service.py`:

```python
"""Tests for backend.services.browser_session helpers."""

from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest

from backend.services import browser_session as bs


@pytest.fixture
async def db():
    """In-memory sqlite with the minimal browser_sessions schema for tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE browser_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            url TEXT NOT NULL,
            feature_name TEXT,
            browser_type TEXT,
            steps_json TEXT,
            metadata_json TEXT DEFAULT '{}',
            status TEXT,
            created_at TEXT
        );
    """)
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()
        os.unlink(path)


async def test_update_feature_name_writes_column(db):
    session = await bs.create_session(
        db, project_id="p1", user_id="u1", url="https://example.com",
        feature_name="",
    )
    await db.commit()

    updated = await bs.update_feature_name(db, session.id, "my_feature")
    await db.commit()

    assert updated is not None
    assert updated.feature_name == "my_feature"

    # Confirm persisted by re-reading.
    re_read = await bs.get_session(db, session.id)
    assert re_read.feature_name == "my_feature"


async def test_update_feature_name_returns_none_for_missing_session(db):
    result = await bs.update_feature_name(db, "bs_does_not_exist", "x")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_browser_session_service.py -v`
Expected: FAIL — `AttributeError: module 'backend.services.browser_session' has no attribute 'update_feature_name'`.

- [ ] **Step 3: Add the helper to `backend/services/browser_session.py`**

Append after the existing `set_metadata` function (end of file):

```python
async def update_feature_name(
    db: aiosqlite.Connection,
    session_id: str,
    feature_name: str,
) -> BrowserSession | None:
    """Update only the feature_name column on an existing session.

    Used by the orchestrator after deriving a feature name from the first
    snapshot when the session was created with an empty feature_name.
    """
    session = await get_session(db, session_id)
    if not session:
        return None
    await db.execute(
        "UPDATE browser_sessions SET feature_name = ? WHERE id = ?",
        (feature_name, session_id),
    )
    return session.model_copy(update={"feature_name": feature_name})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_browser_session_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/services/test_browser_session_service.py backend/services/browser_session.py
git commit -m "browser_session: add update_feature_name helper for derived names"
```

---

## Task 8: TDD — `BrowserMcpDriver` lifecycle (`start`, `close`)

**Files:**
- Create: `tests/browser_explorer/test_mcp_driver.py`
- Modify: `backend/services/browser_explorer/drivers/mcp_driver.py` (add class)

- [ ] **Step 1: Write the failing test**

Create `tests/browser_explorer/test_mcp_driver.py`:

```python
"""Tests for BrowserMcpDriver against a mocked MCP ClientSession."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.browser_explorer.drivers import (
    BrowserNotConnectedError,
    RefNotFoundError,
)
from backend.services.browser_explorer.drivers.mcp_driver import BrowserMcpDriver


def _text_result(text: str) -> MagicMock:
    """Mock a CallToolResult with a single TextContent block."""
    block = MagicMock()
    block.text = text
    block.type = "text"
    result = MagicMock()
    result.content = [block]
    result.isError = False
    return result


def _error_result(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    block.type = "text"
    result = MagicMock()
    result.content = [block]
    result.isError = True
    return result


def _tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _tools_list(*names: str) -> MagicMock:
    """Mock a ListToolsResult with the named tools."""
    result = MagicMock()
    result.tools = [_tool(n) for n in names]
    return result


@asynccontextmanager
async def _stdio_client_mock(read_stream=None, write_stream=None):
    yield (AsyncMock(), AsyncMock())


@pytest.fixture
def mock_mcp(monkeypatch):
    """Patch stdio_client + ClientSession so no real subprocess is spawned."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=_tools_list(
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_type", "browser_screenshot",
    ))
    session.call_tool = AsyncMock()

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "backend.services.browser_explorer.drivers.mcp_driver.stdio_client",
        lambda params: _stdio_client_mock(),
    )
    monkeypatch.setattr(
        "backend.services.browser_explorer.drivers.mcp_driver.ClientSession",
        lambda r, w: session_cm,
    )
    return session


async def test_start_initializes_session_and_lists_tools(mock_mcp):
    driver = BrowserMcpDriver()
    await driver.start()
    mock_mcp.initialize.assert_awaited_once()
    mock_mcp.list_tools.assert_awaited_once()
    await driver.close()


async def test_start_raises_when_required_tool_missing(monkeypatch):
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=_tools_list(
        "browser_navigate"  # missing the others
    ))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "backend.services.browser_explorer.drivers.mcp_driver.stdio_client",
        lambda params: _stdio_client_mock(),
    )
    monkeypatch.setattr(
        "backend.services.browser_explorer.drivers.mcp_driver.ClientSession",
        lambda r, w: session_cm,
    )

    driver = BrowserMcpDriver()
    with pytest.raises(RuntimeError, match="missing required tools"):
        await driver.start()


async def test_close_is_idempotent(mock_mcp):
    driver = BrowserMcpDriver()
    await driver.start()
    await driver.close()
    await driver.close()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: FAIL — `ImportError` or `AttributeError` for `BrowserMcpDriver`'s methods (the stub raises `NotImplementedError`).

- [ ] **Step 3: Replace the stub class in `mcp_driver.py`**

In `backend/services/browser_explorer/drivers/mcp_driver.py`, append the class implementation (after the existing parser code from Task 5):

```python
import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.services.browser_explorer.drivers import (
    BrowserNotConnectedError,
    RefNotFoundError,
    Snapshot,
)

logger = logging.getLogger(__name__)


_REQUIRED_TOOLS = frozenset({
    "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
})

# Heuristic substring matches on browsermcp.io error messages.
# If upstream changes their copy, update these — exposed as module-level so
# tests and overrides can patch them.
_NOT_CONNECTED_PATTERNS = (
    "not connected",
    "no tab",
    "connect the browser",
    "browser mcp extension",
)
_STALE_REF_PATTERNS = (
    "ref not found",
    "unknown ref",
    "stale ref",
    "no element with ref",
)


def _classify_error(msg: str) -> Exception:
    low = msg.lower()
    if any(p in low for p in _NOT_CONNECTED_PATTERNS):
        return BrowserNotConnectedError(msg)
    if any(p in low for p in _STALE_REF_PATTERNS):
        return RefNotFoundError(msg)
    return RuntimeError(msg)


class BrowserMcpDriver:
    """BrowserDriver implementation that proxies to browsermcp.io's MCP server.

    Lifecycle: ``start()`` spawns ``@browsermcp/mcp`` over stdio, opens an
    MCP ``ClientSession``, runs the initialize handshake, and verifies the
    expected tools are advertised. ``close()`` tears down the exit stack,
    which terminates the subprocess.

    Caches accessibility names from the most recent snapshot so ``click`` /
    ``type`` can pass the human-readable ``element`` arg expected by
    browsermcp's tools.
    """

    def __init__(
        self,
        *,
        mcp_command: str = "npx",
        mcp_args: list[str] | None = None,
        startup_timeout_seconds: float = 30.0,
        tool_timeout_seconds: float = 30.0,
    ):
        self._command = mcp_command
        self._args = list(mcp_args or ["-y", "@browsermcp/mcp@latest"])
        self._startup_timeout = startup_timeout_seconds
        self._tool_timeout = tool_timeout_seconds
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._element_names: dict[str, str] = {}
        self._cached_url: str = ""
        self._cached_title: str = ""

    async def start(self) -> None:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=self._command, args=self._args)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=self._startup_timeout)
            tools_resp = await asyncio.wait_for(session.list_tools(), timeout=self._startup_timeout)
            advertised = {t.name for t in tools_resp.tools}
            missing = _REQUIRED_TOOLS - advertised
            if missing:
                raise RuntimeError(
                    f"browsermcp.io MCP server is missing required tools: {sorted(missing)}. "
                    f"Got: {sorted(advertised)}"
                )
            self._session = session
            self._exit_stack = stack
        except BaseException:
            await stack.aclose()
            raise

    async def close(self) -> None:
        if self._exit_stack is None:
            return
        try:
            await self._exit_stack.aclose()
        except ProcessLookupError:
            logger.debug("subprocess already exited during close")
        except Exception:
            logger.exception("error during MCP driver close")
        finally:
            self._exit_stack = None
            self._session = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 3 passed (the three lifecycle tests). The remaining tests in this file are for later tasks; they'll fail with `AttributeError` for missing methods — that's expected.

If pytest complains about no tests collected (e.g. async fixture error), check that `asyncio_mode = auto` is in `pytest.ini`.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_mcp_driver.py backend/services/browser_explorer/drivers/mcp_driver.py
git commit -m "drivers/mcp: implement start/close lifecycle with tool validation"
```

---

## Task 9: TDD — `BrowserMcpDriver.navigate` and `snapshot`

**Files:**
- Modify: `tests/browser_explorer/test_mcp_driver.py` (add tests)
- Modify: `backend/services/browser_explorer/drivers/mcp_driver.py` (add methods)

- [ ] **Step 1: Append failing tests to `test_mcp_driver.py`**

Add at the end of `tests/browser_explorer/test_mcp_driver.py`:

```python
SNAPSHOT_TEXT = """- Page URL: https://example.com/dashboard
- Page Title: Dashboard
- button "Apply" [ref=e4]
- textbox "Search" [ref=e3]"""


async def test_navigate_calls_browser_navigate_tool(mock_mcp):
    mock_mcp.call_tool.return_value = _text_result("ok")
    driver = BrowserMcpDriver()
    await driver.start()

    await driver.navigate("https://example.com/dashboard")

    mock_mcp.call_tool.assert_awaited_with(
        "browser_navigate", {"url": "https://example.com/dashboard"}
    )
    await driver.close()


async def test_snapshot_calls_browser_snapshot_and_parses(mock_mcp):
    mock_mcp.call_tool.return_value = _text_result(SNAPSHOT_TEXT)
    driver = BrowserMcpDriver()
    await driver.start()

    snap = await driver.snapshot()

    mock_mcp.call_tool.assert_awaited_with("browser_snapshot", {})
    assert snap["url"] == "https://example.com/dashboard"
    assert snap["title"] == "Dashboard"
    assert len(snap["elements"]) == 2
    await driver.close()


async def test_snapshot_caches_element_names_url_title(mock_mcp):
    mock_mcp.call_tool.return_value = _text_result(SNAPSHOT_TEXT)
    driver = BrowserMcpDriver()
    await driver.start()
    await driver.snapshot()

    assert await driver.current_url() == "https://example.com/dashboard"
    assert await driver.page_title() == "Dashboard"
    # Internal cache exposed for click/type
    assert driver._element_names["e4"] == "Apply"
    assert driver._element_names["e3"] == "Search"
    await driver.close()


async def test_navigate_surfaces_not_connected_error(mock_mcp):
    mock_mcp.call_tool.return_value = _error_result(
        "Browser MCP extension is not connected. Click 'Connect' on the extension."
    )
    driver = BrowserMcpDriver()
    await driver.start()

    with pytest.raises(BrowserNotConnectedError):
        await driver.navigate("https://example.com")

    await driver.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 3 pass (lifecycle), 4 new fail with `AttributeError: 'BrowserMcpDriver' object has no attribute 'navigate'`.

- [ ] **Step 3: Add `navigate`, `snapshot`, `current_url`, `page_title`, and a private `_call` helper to the driver class**

Append to the `BrowserMcpDriver` class (inside the class body):

```python
    async def _call(self, name: str, args: dict[str, Any]) -> str:
        """Call an MCP tool, return the joined text content, raise mapped
        exceptions on errors.
        """
        if self._session is None:
            raise RuntimeError("driver not started; call start() first")
        result = await asyncio.wait_for(
            self._session.call_tool(name, args), timeout=self._tool_timeout
        )
        text_blocks = [
            getattr(b, "text", "") for b in (result.content or [])
            if getattr(b, "type", "") == "text"
        ]
        text = "\n".join(text_blocks).strip()
        if getattr(result, "isError", False):
            raise _classify_error(text or f"MCP tool {name!r} returned an error")
        return text

    async def navigate(self, url: str) -> None:
        await self._call("browser_navigate", {"url": url})
        self._cached_url = url  # snapshot will overwrite with the real post-redirect URL

    async def snapshot(self) -> Snapshot:
        text = await self._call("browser_snapshot", {})
        snap = _parse_browsermcp_snapshot(text)
        self._cached_url = snap.get("url", "") or self._cached_url
        self._cached_title = snap.get("title", "") or self._cached_title
        self._element_names = {
            el["ref"]: el.get("name", "") for el in (snap.get("elements") or [])
        }
        return snap

    async def current_url(self) -> str:
        return self._cached_url

    async def page_title(self) -> str:
        return self._cached_title
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_mcp_driver.py backend/services/browser_explorer/drivers/mcp_driver.py
git commit -m "drivers/mcp: implement navigate, snapshot, url/title caching"
```

---

## Task 10: TDD — `BrowserMcpDriver.click` and `type`

**Files:**
- Modify: `tests/browser_explorer/test_mcp_driver.py`
- Modify: `backend/services/browser_explorer/drivers/mcp_driver.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/browser_explorer/test_mcp_driver.py`:

```python
async def test_click_passes_cached_element_name(mock_mcp):
    # First call returns snapshot to populate the name cache, then click result.
    mock_mcp.call_tool.side_effect = [
        _text_result(SNAPSHOT_TEXT),
        _text_result("clicked"),
    ]
    driver = BrowserMcpDriver()
    await driver.start()
    await driver.snapshot()

    result = await driver.click("e4")

    args = mock_mcp.call_tool.await_args_list[-1].args
    assert args[0] == "browser_click"
    assert args[1] == {"ref": "e4", "element": "Apply"}
    assert result == {"ok": True}
    await driver.close()


async def test_click_with_unknown_ref_still_calls_tool_with_blank_name(mock_mcp):
    """If we click before snapshotting (orchestrator shouldn't, but defend),
    pass an empty element name and let the server respond."""
    mock_mcp.call_tool.return_value = _text_result("clicked")
    driver = BrowserMcpDriver()
    await driver.start()

    await driver.click("e99")

    args = mock_mcp.call_tool.await_args_list[-1].args
    assert args[1] == {"ref": "e99", "element": ""}
    await driver.close()


async def test_click_stale_ref_raises_RefNotFoundError(mock_mcp):
    mock_mcp.call_tool.side_effect = [
        _text_result(SNAPSHOT_TEXT),
        _error_result("Ref not found: e4 is no longer in the DOM"),
    ]
    driver = BrowserMcpDriver()
    await driver.start()
    await driver.snapshot()

    with pytest.raises(RefNotFoundError):
        await driver.click("e4")

    await driver.close()


async def test_type_passes_text_and_element(mock_mcp):
    mock_mcp.call_tool.side_effect = [
        _text_result(SNAPSHOT_TEXT),
        _text_result("typed"),
    ]
    driver = BrowserMcpDriver()
    await driver.start()
    await driver.snapshot()

    result = await driver.type("e3", "hello@example.com")

    args = mock_mcp.call_tool.await_args_list[-1].args
    assert args[0] == "browser_type"
    assert args[1] == {
        "ref": "e3",
        "element": "Search",
        "text": "hello@example.com",
        "submit": False,
    }
    assert result == {"ok": True}
    await driver.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 7 pass, 4 new fail with `AttributeError`.

- [ ] **Step 3: Add `click` and `type` methods**

Append to the `BrowserMcpDriver` class:

```python
    async def click(self, ref: str) -> dict[str, Any]:
        element = self._element_names.get(ref, "")
        await self._call("browser_click", {"ref": ref, "element": element})
        return {"ok": True}

    async def type(self, ref: str, value: str) -> dict[str, Any]:
        element = self._element_names.get(ref, "")
        await self._call("browser_type", {
            "ref": ref,
            "element": element,
            "text": value,
            "submit": False,
        })
        return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_mcp_driver.py backend/services/browser_explorer/drivers/mcp_driver.py
git commit -m "drivers/mcp: implement click and type with element-name caching"
```

---

## Task 11: TDD — `BrowserMcpDriver.screenshot`

**Files:**
- Modify: `tests/browser_explorer/test_mcp_driver.py`
- Modify: `backend/services/browser_explorer/drivers/mcp_driver.py`

browsermcp.io's `browser_screenshot` returns a base64-encoded PNG inside an `ImageContent` block. We decode and write to disk.

- [ ] **Step 1: Append failing test**

Append to `tests/browser_explorer/test_mcp_driver.py`:

```python
def _image_result(b64_png: str) -> MagicMock:
    block = MagicMock()
    block.type = "image"
    block.data = b64_png
    block.mimeType = "image/png"
    result = MagicMock()
    result.content = [block]
    result.isError = False
    return result


async def test_screenshot_decodes_base64_and_writes_file(mock_mcp, tmp_path):
    # 1x1 transparent PNG
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    import base64 as b64
    mock_mcp.call_tool.return_value = _image_result(b64.b64encode(png_bytes).decode())

    driver = BrowserMcpDriver()
    await driver.start()

    out = str(tmp_path / "shot.png")
    returned = await driver.screenshot(out)

    assert returned == out
    assert open(out, "rb").read().startswith(b"\x89PNG")
    mock_mcp.call_tool.assert_awaited_with("browser_screenshot", {})
    await driver.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/browser_explorer/test_mcp_driver.py::test_screenshot_decodes_base64_and_writes_file -v`
Expected: FAIL — `AttributeError: 'BrowserMcpDriver' object has no attribute 'screenshot'`.

- [ ] **Step 3: Add `screenshot` method**

Add to the top-of-file imports in `mcp_driver.py`:

```python
import base64
```

Append to the `BrowserMcpDriver` class:

```python
    async def screenshot(self, path: str) -> str:
        if self._session is None:
            raise RuntimeError("driver not started; call start() first")
        result = await asyncio.wait_for(
            self._session.call_tool("browser_screenshot", {}),
            timeout=self._tool_timeout,
        )
        if getattr(result, "isError", False):
            text = "\n".join(getattr(b, "text", "") for b in (result.content or []))
            raise _classify_error(text or "browser_screenshot returned an error")
        # Find an image block.
        for block in result.content or []:
            if getattr(block, "type", "") == "image":
                png = base64.b64decode(block.data)
                with open(path, "wb") as f:
                    f.write(png)
                return path
        raise RuntimeError("browser_screenshot returned no image content")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/browser_explorer/test_mcp_driver.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_mcp_driver.py backend/services/browser_explorer/drivers/mcp_driver.py
git commit -m "drivers/mcp: implement screenshot via base64 image block"
```

---

## Task 12: Wire `BrowserMcpDriver` into `_build_driver` with settings

**Files:**
- Modify: `backend/services/browser_explorer/__init__.py:53-59`
- Modify: `backend/services/browser_explorer/__init__.py:113-160` (pass `settings` to `_build_driver`)

- [ ] **Step 1: Add a wiring test**

Append to `tests/browser_explorer/test_mcp_driver.py`:

```python
def test_build_driver_mcp_passes_settings(monkeypatch):
    """_build_driver('mcp', ...) should construct a BrowserMcpDriver
    using the values from Settings."""
    from backend.services.browser_explorer import _build_driver
    from backend.config import Settings

    settings = Settings(
        browser_mcp_command="my-npx",
        browser_mcp_args="--foo --bar baz",
        browser_mcp_startup_timeout_seconds=45,
        browser_mcp_tool_timeout_seconds=20,
    )
    driver = _build_driver(
        "mcp", headless=True, host_allowlist=None, settings=settings,
    )
    assert isinstance(driver, BrowserMcpDriver)
    assert driver._command == "my-npx"
    assert driver._args == ["--foo", "--bar", "baz"]
    assert driver._startup_timeout == 45.0
    assert driver._tool_timeout == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/browser_explorer/test_mcp_driver.py::test_build_driver_mcp_passes_settings -v`
Expected: FAIL — `_build_driver` either doesn't accept `settings` kwarg, or returns the stub.

- [ ] **Step 3: Update `_build_driver` and its caller**

In `backend/services/browser_explorer/__init__.py`, replace the existing `_build_driver` (lines 53-59):

```python
def _build_driver(
    name: str,
    *,
    headless: bool,
    host_allowlist: list[str] | None,
    settings: Settings,
) -> BrowserDriver:
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

Then update the call site in `_run_exploration_impl` (around line 143):

```python
    driver = _build_driver(
        driver_name, headless=headless, host_allowlist=host_allowlist, settings=s,
    )
```

- [ ] **Step 4: Run all driver + wiring tests to verify they pass**

Run: `pytest tests/browser_explorer/ -v`
Expected: 13+ passed (12 driver + 1 wiring).

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add backend/services/browser_explorer/__init__.py tests/browser_explorer/test_mcp_driver.py
git commit -m "browser_explorer: thread Settings into _build_driver for MCP driver"
```

---

## Task 13: Make `feature_name` optional in `StartSessionBody`

**Files:**
- Modify: `backend/models/browser_session.py`

- [ ] **Step 1: Read current `StartSessionBody`**

Run: `grep -n "class StartSessionBody" backend/models/browser_session.py`
Confirm the field's current shape so the edit is minimal and correct.

- [ ] **Step 2: Update the model**

In `backend/models/browser_session.py`, change `feature_name` from required to optional with a `""` default:

```python
class StartSessionBody(BaseModel):
    project_id: str
    url: str
    feature_name: str = ""        # optional — orchestrator derives from page title if empty
    browser_type: str = "playwright"
    steps: list[str] | None = None
```

- [ ] **Step 3: Add a smoke test**

Create `tests/test_browser_session_models.py`:

```python
"""Pydantic model tests for browser_session API bodies."""

from backend.models.browser_session import StartSessionBody


def test_start_session_body_accepts_missing_feature_name():
    body = StartSessionBody(project_id="p1", url="https://example.com")
    assert body.feature_name == ""


def test_start_session_body_accepts_explicit_feature_name():
    body = StartSessionBody(
        project_id="p1", url="https://example.com", feature_name="Login",
    )
    assert body.feature_name == "Login"
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_browser_session_models.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add backend/models/browser_session.py tests/test_browser_session_models.py
git commit -m "models/browser_session: make feature_name optional"
```

---

## Task 14: Make `goal` optional in `ExploreStartBody`; drop empty-goal guard

**Files:**
- Modify: `backend/routers/browser_session.py:47-54` (model)
- Modify: `backend/routers/browser_session.py:204-205` (guard)

- [ ] **Step 1: Add a route test**

Create `tests/routers/__init__.py` (empty) and `tests/routers/test_browser_session_routes.py`:

```python
"""Smoke tests for the browser_session router — checks empty goal accepted."""

from backend.routers.browser_session import ExploreStartBody


def test_explore_start_body_accepts_empty_goal():
    body = ExploreStartBody()
    assert body.goal == ""


def test_explore_start_body_accepts_explicit_goal():
    body = ExploreStartBody(goal="Test the login flow")
    assert body.goal == "Test the login flow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/routers/test_browser_session_routes.py -v`
Expected: FAIL — Pydantic validation error: `goal` is required.

- [ ] **Step 3: Update the model and drop the guard**

In `backend/routers/browser_session.py`, replace the `ExploreStartBody` class:

```python
class ExploreStartBody(BaseModel):
    goal: str = ""               # optional — orchestrator derives from page if empty
    max_actions: int | None = None
    max_pages: int | None = None
    max_seconds: int | None = None
    driver: str | None = None    # "playwright" | "mcp"
    read_only: bool | None = None
    headless: bool = True
```

Then in the `start_exploration` route handler, **remove** the empty-goal guard (around line 204):

```python
    if not (body.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")
```

Delete those two lines entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/routers/test_browser_session_routes.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

Request user approval, then:

```bash
git add backend/routers/browser_session.py tests/routers/
git commit -m "routers/browser_session: make goal optional, drop empty-goal 400 guard"
```

---

## Task 15: Wire goal/feature derivation into `Orchestrator.run()` with persistence

**Files:**
- Modify: `backend/services/browser_explorer/orchestrator.py:136-200` (after first snapshot)
- Test: `tests/browser_explorer/test_orchestrator_derivation.py` (new — uses a fake driver, no MCP/Playwright)

- [ ] **Step 1: Read the current `run()` method**

Run: `sed -n '130,210p' backend/services/browser_explorer/orchestrator.py`
Identify exactly where the first `driver.snapshot()` is called, where `ledger.pages[0]` is recorded, and where the LLM tool loop starts. Confirm the right insertion point: derivation should happen *between* the first snapshot and the start of the loop.

- [ ] **Step 2: Locate where the orchestrator currently takes the first snapshot**

Run:

```bash
grep -n "driver\.snapshot\|driver\.navigate" backend/services/browser_explorer/orchestrator.py
```

Note the line of the first `driver.snapshot()` call inside `run()`. The derivation hook goes right after that line, before the LLM tool loop.

- [ ] **Step 3: Write a fake-driver-backed integration test**

Create `tests/browser_explorer/test_orchestrator_derivation.py`:

```python
"""Tests that Orchestrator persists derived goal/feature_name when callers
omit them. Uses a fake driver so MCP/Playwright are not exercised here."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import aiosqlite
import pytest

from backend.services.browser_explorer.drivers import Snapshot


SNAPSHOT_FIXTURE = Snapshot({
    "url": "https://example.com/dashboard",
    "title": "Dashboard",
    "elements": [
        {"ref": "e1", "role": "button", "name": "Apply", "disabled": False,
         "tag": "", "testid": None, "text": "", "type": None},
        {"ref": "e2", "role": "textbox", "name": "Search", "disabled": False,
         "tag": "", "testid": None, "text": "", "type": None},
    ],
    "text_dump": "",
    "summary": "",
})


class FakeDriver:
    """Minimal BrowserDriver that returns a canned snapshot."""

    def __init__(self) -> None:
        self.started = False
        self.navigated_to: str | None = None
        self.snapshots_taken = 0

    async def start(self) -> None:
        self.started = True

    async def navigate(self, url: str) -> None:
        self.navigated_to = url

    async def snapshot(self) -> Snapshot:
        self.snapshots_taken += 1
        return SNAPSHOT_FIXTURE

    async def click(self, ref: str) -> dict[str, Any]:
        return {"ok": True}

    async def type(self, ref: str, value: str) -> dict[str, Any]:
        return {"ok": True}

    async def screenshot(self, path: str) -> str:
        return path

    async def current_url(self) -> str:
        return SNAPSHOT_FIXTURE["url"]

    async def page_title(self) -> str:
        return SNAPSHOT_FIXTURE["title"]

    async def close(self) -> None:
        self.started = False


async def test_derivation_runs_after_first_snapshot_when_goal_empty():
    """Direct unit test of the derivation path. Asserts that calling the
    helpers with the fake snapshot yields the expected derived strings —
    proves the helpers are pure and ready to be wired in.
    """
    from backend.services.browser_explorer.orchestrator import (
        _derive_feature_name, _derive_goal_from_snapshot,
    )

    goal = _derive_goal_from_snapshot(SNAPSHOT_FIXTURE)
    assert "Dashboard" in goal
    assert "1 input field" in goal
    assert "1 primary action" in goal

    fname = _derive_feature_name(SNAPSHOT_FIXTURE, "bs_abc12345def6")
    assert fname == "dashboard"
```

- [ ] **Step 4: Run test to verify it passes (helpers already exist from Task 6)**

Run: `pytest tests/browser_explorer/test_orchestrator_derivation.py -v`
Expected: 1 passed.

- [ ] **Step 5: Wire derivation into `Orchestrator.run()`**

In `backend/services/browser_explorer/orchestrator.py`, locate the first `driver.snapshot()` call inside `run()` (the one that seeds `ledger.pages[0]`). Immediately after that call, but before the LLM tool loop kicks off, insert:

```python
        # ---- Derive goal/feature_name from the first snapshot if caller omitted them ----
        derived_meta: dict[str, Any] = {}
        if not (self.ledger.goal or "").strip():
            derived_goal = _derive_goal_from_snapshot(first_snapshot)
            self.ledger.goal = derived_goal
            derived_meta["goal"] = derived_goal
            derived_meta["goal_source"] = "auto"
        else:
            derived_meta["goal_source"] = "user"

        # The session row may have empty feature_name; derive + persist if so.
        # This requires a DB write, kept here (not in the route) because we
        # need the snapshot.
        async with get_db() as _db:
            session = await bs_service.get_session(_db, self.ledger.session_id)
            if session and not (session.feature_name or "").strip():
                fname = _derive_feature_name(first_snapshot, self.ledger.session_id)
                await bs_service.update_feature_name(_db, self.ledger.session_id, fname)
                derived_meta["feature_name"] = fname
            await bs_service.set_metadata(_db, self.ledger.session_id, derived_meta)
            await _db.commit()
```

You'll need to:

1. Replace `first_snapshot` with whatever variable the existing code uses to hold the first snapshot result. Read the surrounding 30 lines and use the same name.
2. Add these imports at the top of `orchestrator.py` if not already present:

```python
from backend.db import get_db
from backend.services import browser_session as bs_service
```

3. Confirm `self.ledger.goal` and `self.ledger.session_id` are accessible attributes. If `ledger.goal` doesn't exist as a writable field, store the derived goal on the orchestrator instead (`self._derived_goal`) and pass it to whatever currently builds the system prompt for the tool loop.

**Read-and-verify gate:** Before moving on, run `pytest tests/browser_explorer/ -v` and confirm no existing tests broke. The orchestrator module imports cleanly; if the new imports cause a circular import, switch to local imports inside the derivation block.

- [ ] **Step 6: Add an end-to-end orchestrator derivation test using the fake driver**

Append to `tests/browser_explorer/test_orchestrator_derivation.py`:

```python
async def test_orchestrator_persists_derived_goal_and_feature_name(monkeypatch, tmp_path):
    """Run Orchestrator.run() with a FakeDriver and an empty goal/feature_name;
    verify the derived values land in the BrowserSession metadata + column.
    """
    from backend.services.browser_explorer.orchestrator import Orchestrator
    from backend.services.browser_explorer.ledger import ExplorationLedger
    from backend.services.browser_explorer.budget import Budget
    from backend.config import Settings
    from backend.services import browser_session as bs_service
    from backend.db import get_db

    # Stub out the LLM tool loop so the test doesn't need an API key.
    async def _fake_run_tool_loop(*args, **kwargs):
        from backend.services.llm_tool_loop import ToolLoopResult
        return ToolLoopResult(stopped="done", turns=0, last_tool=None, error=None)

    monkeypatch.setattr(
        "backend.services.browser_explorer.orchestrator.run_tool_loop",
        _fake_run_tool_loop,
    )

    # Create a session row with empty feature_name + empty goal.
    async with get_db() as db:
        session = await bs_service.create_session(
            db, project_id="p1", user_id="u1",
            url="https://example.com/dashboard", feature_name="",
        )
        await db.commit()

    ledger = ExplorationLedger(
        session_id=session.id, goal="", starting_url="https://example.com/dashboard",
    )
    orch = Orchestrator(
        driver=FakeDriver(),
        ledger=ledger,
        budget=Budget(max_actions=1, max_pages=1, max_seconds=10),
        settings=Settings(),
        model_id="gpt-4o-mini",  # never actually called
        screenshot_dir=str(tmp_path),
        read_only=True,
        on_progress=None,
    )

    await orch.run()

    async with get_db() as db:
        refreshed = await bs_service.get_session(db, session.id)

    assert refreshed.feature_name == "dashboard"
    assert refreshed.metadata.get("goal_source") == "auto"
    assert "Dashboard" in (refreshed.metadata.get("goal") or "")
```

- [ ] **Step 7: Run the new test**

Run: `pytest tests/browser_explorer/test_orchestrator_derivation.py -v`
Expected: 2 passed. If the orchestrator's `run()` shape doesn't match the assumptions in Step 5, fix the wire-in and re-run.

- [ ] **Step 8: Run the entire test suite to catch regressions**

Run: `pytest -v`
Expected: All passing. If anything else breaks, investigate before committing.

- [ ] **Step 9: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_orchestrator_derivation.py backend/services/browser_explorer/orchestrator.py
git commit -m "orchestrator: derive and persist goal/feature_name from first snapshot"
```

---

## Task 16: Live integration smoke test (gated)

**Files:**
- Create: `tests/browser_explorer/test_mcp_driver_live.py`

This test is **only** for manual local verification; it spawns a real `npx @browsermcp/mcp` and assumes the user has the Chrome extension connected.

- [ ] **Step 1: Create the gated test**

Create `tests/browser_explorer/test_mcp_driver_live.py`:

```python
"""Live integration smoke test for BrowserMcpDriver.

Requires:
  - Node.js / npx on PATH
  - browsermcp.io Chrome extension installed
  - User has clicked "Connect" on a tab in Chrome

Run with:  BROWSERMCP_LIVE=1 pytest tests/browser_explorer/test_mcp_driver_live.py -v -s

Skipped by default — never runs in CI.
"""

from __future__ import annotations

import os

import pytest

from backend.services.browser_explorer.drivers.mcp_driver import BrowserMcpDriver


pytestmark = pytest.mark.skipif(
    not os.getenv("BROWSERMCP_LIVE"),
    reason="set BROWSERMCP_LIVE=1 to run live browsermcp.io integration tests",
)


async def test_live_start_navigate_snapshot_close():
    driver = BrowserMcpDriver()
    await driver.start()
    try:
        await driver.navigate("https://example.com")
        snap = await driver.snapshot()
        # example.com has no interactive elements aside from a single link.
        assert snap["title"]  # should be non-empty
        assert "example.com" in snap["url"]
    finally:
        await driver.close()
```

- [ ] **Step 2: Verify the test is collected but skipped without the env var**

Run: `pytest tests/browser_explorer/test_mcp_driver_live.py -v`
Expected: 1 skipped.

- [ ] **Step 3: Commit**

Request user approval, then:

```bash
git add tests/browser_explorer/test_mcp_driver_live.py
git commit -m "drivers/mcp: add gated live integration smoke test"
```

---

## Task 17: Manual end-to-end verification

This is **not** code — it's the smoke checklist for the user to run before merging.

- [ ] **Step 1: Start the backend**

Run: `uvicorn backend.main:app --reload`
Expected: backend on `http://127.0.0.1:8000`.

- [ ] **Step 2: Install browsermcp.io extension**

In Chrome: install the browsermcp.io extension from the Chrome Web Store. Open a tab on whatever post-login URL you want to test. Click the extension icon → "Connect".

- [ ] **Step 3: Create a session with no feature_name**

Run:

```bash
curl -X POST http://127.0.0.1:8000/api/browser-session/start \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<your-project-id>","url":"<your-post-login-url>"}'
```

Expected: `200 OK` with a `session.id` like `bs_xxxxxxxx`. Note the ID.

- [ ] **Step 4: Kick off explore with no goal, driver=mcp**

Run:

```bash
curl -X POST http://127.0.0.1:8000/api/browser-session/<id>/explore \
  -H "Content-Type: application/json" \
  -d '{"driver":"mcp","headless":true}'
```

Expected: `{"session_id":"...","status":"running"}`.

- [ ] **Step 5: Poll status**

Run repeatedly:

```bash
curl http://127.0.0.1:8000/api/browser-session/<id>/explore/status
```

Expected progression:
- `actions_count` increases over ~30s.
- `current_url` updates as the explorer navigates within the page.
- Eventually `status` → `done` (or `error` if something went wrong; check backend logs).

- [ ] **Step 6: Inspect the session**

Run:

```bash
curl http://127.0.0.1:8000/api/browser-session/<id>
```

Expected:
- `feature_name` is populated with a slug derived from the page title.
- `metadata.goal` contains the derived goal text.
- `metadata.goal_source == "auto"`.
- `metadata.evidence_ledger.pages[0].title` matches your real authenticated page.

- [ ] **Step 7: Generate test cases from the session**

In the frontend, open the project, switch to "Browser Session" parser, paste the `session_id`, click Generate. Confirm the resulting test cases reference real elements from your authenticated page (button names, form fields).

- [ ] **Step 8: Failure-mode check — disconnect the extension and retry**

Click the extension icon → "Disconnect". Re-run Step 4. Expected: explore status becomes `error` quickly with a message about the browser not being connected (driver raised `BrowserNotConnectedError`).

If any of the above steps misbehave, file a bug with the backend logs and the session metadata before merging.

- [ ] **Step 9: No commit** — this task is verification only.

---

## Self-Review Notes

- Spec coverage: every section of the design doc maps to a task. Tool mapping (Tasks 9–11), error taxonomy (Tasks 8–10 via `_classify_error`), settings (Task 2), derivation (Tasks 6, 15), persistence (Tasks 7, 15), testing strategy (covered Layer 1+2 across Tasks 5–11, Layer 3 in Task 16, manual checklist in Task 17).
- Type/name consistency: `_parse_browsermcp_snapshot` returns a `Snapshot` (dict subclass) in Task 5 and is consumed via `.get()` in Tasks 6, 9, 11, 15 — consistent.
- The orchestrator task (15) has a documented "read first, then wire" gate because it depends on existing variable names inside `run()` that aren't fully visible in the spec.
