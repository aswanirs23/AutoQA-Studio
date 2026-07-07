# Authenticated Auto-Execute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user configure login once per project so every auto-execute Playwright run starts already authenticated, reusing a captured `storage_state`, with credentials never appearing in generated test code.

**Architecture:** Store per-project login config in a new additive `projects.auth_config` JSON column (same storage posture as existing API keys — SQLite, masked). A server-assembled login script runs in the existing sandboxed subprocess to capture Playwright `storage_state` into gitignored `data/auth/<project_id>.json`. The test runner loads that state into the browser context so tests are authenticated; on a detected expired session it re-captures once and retries.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite/SQLite, Playwright (async, Chromium), pytest (`asyncio_mode=auto`), vanilla-JS SPA.

## Global Constraints

- Schema changes are **additive only**, via `_ensure_column()` in `backend/db.py` — never edit historical DDL.
- Credentials/secrets live in SQLite only: never in `.env`, never in git (`data/` is gitignored), never in any LLM prompt, never written into generated test code; **masked** whenever returned via the API.
- The user-code **denylist and env-scrub in `playwright_runner.py` stay intact**; the trusted server-assembled login script is exempt from the denylist but still runs in the isolated subprocess (scrubbed env, 60s timeout).
- Chromium only; 60-second subprocess timeout.
- One uvicorn worker / SQLite (no concurrency assumptions).
- Commit messages: **no `Co-Authored-By` trailer** (see `CLAUDE.md`).
- Run the full suite with `python -m pytest -q` before each commit that touches backend.

---

### Task 1: `auth_config` column, Project model field, and repo read/write (masked)

**Files:**
- Modify: `backend/db.py` (add `_ensure_column` for `auth_config`, ~line 207)
- Modify: `backend/models/test_case.py` (`Project` model, ~line 18)
- Modify: `backend/repositories/project_repo.py` (`get_project` SELECT + new `get_project_auth`/`update_project_auth`)
- Test: `tests/repositories/test_project_auth.py`

**Interfaces:**
- Produces:
  - `Project.auth_config: dict` (defaults `{}`)
  - `project_repo.get_project_auth(db, user_id, project_id) -> dict | None` — raw, **unmasked** auth_config for internal login use
  - `project_repo.update_project_auth(db, user_id, project_id, auth_config: dict) -> bool`
  - `get_project(...)` returns `Project` with `auth_config` populated (raw; masking happens at the router layer in Task 5)

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_project_auth.py
import pytest
from backend.db import get_db, init_db
from backend.repositories import project_repo


async def _mk_project(db):
    p = await project_repo.create_project(db, "u1", "Proj", "desc")
    return p.id


async def test_update_and_get_auth_config_round_trip():
    await init_db()
    async with get_db() as db:
        pid = await _mk_project(db)
        ok = await project_repo.update_project_auth(
            db, "u1", pid,
            {"login_url": "http://x/login", "username": "u", "password": "p",
             "selectors": {}, "success_check": "/home", "verified_at": ""},
        )
        assert ok is True
        auth = await project_repo.get_project_auth(db, "u1", pid)
        assert auth["username"] == "u"
        assert auth["password"] == "p"
        # And it is exposed on the Project model (raw at repo layer)
        proj = await project_repo.get_project(db, "u1", pid)
        assert proj.auth_config["login_url"] == "http://x/login"


async def test_get_auth_for_missing_project_returns_none():
    await init_db()
    async with get_db() as db:
        assert await project_repo.get_project_auth(db, "u1", "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/repositories/test_project_auth.py -v`
Expected: FAIL (`AttributeError: auth_config` / `update_project_auth` not defined).

- [ ] **Step 3: Add the migration in `backend/db.py`**

Insert after the `playwright_code` `_ensure_column` block (added earlier), before `async def init_db`:

```python
    await _ensure_column(
        db,
        "projects",
        "auth_config",
        "ALTER TABLE projects ADD COLUMN auth_config TEXT NOT NULL DEFAULT '{}'",
    )
```

- [ ] **Step 4: Add `auth_config` to the `Project` model**

In `backend/models/test_case.py`, in class `Project`, after `base_url: str = ""`:

```python
    auth_config: dict[str, Any] = Field(default_factory=dict)
```

(`Any` and `Field` are already imported in this file.)

- [ ] **Step 5: Read + write `auth_config` in `project_repo.py`**

In `get_project`, add `auth_config` to the SELECT column list and to the `Project(...)` construction:

```python
        "SELECT id, user_id, name, description, base_url, context, auth_config, created_at, updated_at FROM projects WHERE id = ? AND user_id = ?",
```
```python
        auth_config=json.loads(row["auth_config"] or "{}"),
```

Append two functions at the end of the file:

```python
async def get_project_auth(db: aiosqlite.Connection, user_id: str, project_id: str) -> dict | None:
    """Raw (unmasked) auth_config for a project, or None if the project is absent."""
    row = await fetch_one(
        db,
        "SELECT auth_config FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    )
    if not row:
        return None
    return json.loads(row["auth_config"] or "{}")


async def update_project_auth(
    db: aiosqlite.Connection, user_id: str, project_id: str, auth_config: dict
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(auth_config, ensure_ascii=False)
    cur = await db.execute(
        "UPDATE projects SET auth_config = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (blob, now, project_id, user_id),
    )
    return cur.rowcount > 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/repositories/test_project_auth.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/models/test_case.py backend/repositories/project_repo.py tests/repositories/test_project_auth.py
git commit -m "feat: add per-project auth_config storage"
```

---

### Task 2: Pure helpers — masking, login-script assembly, expiry detection

**Files:**
- Create: `backend/services/playwright_login.py`
- Test: `tests/services/test_playwright_login_helpers.py`

**Interfaces:**
- Produces (all pure/sync, no I/O):
  - `mask_auth_config(auth: dict) -> dict` — returns a copy with `password` replaced by `{"set": bool}` under key `password_set` and the raw `password` removed; other keys pass through.
  - `auth_storage_path(project_id: str) -> pathlib.Path` — `data/auth/<project_id>.json` under the configured DB directory's parent `data/`.
  - `build_login_script(auth: dict, base_url: str, storage_path: str, headless: bool) -> str` — returns a complete Python script (from a template) that logs in and writes storage_state.
  - `looks_like_login_page(final_url: str, page_text: str, login_url: str) -> bool` — expiry heuristic.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_playwright_login_helpers.py
from backend.services.playwright_login import (
    mask_auth_config, auth_storage_path, looks_like_login_page, build_login_script,
)


def test_mask_hides_password():
    masked = mask_auth_config({"login_url": "u", "username": "bob", "password": "s3cret"})
    assert "password" not in masked
    assert masked["password_set"] is True
    assert masked["username"] == "bob"
    assert mask_auth_config({"username": "x"})["password_set"] is False


def test_storage_path_uses_project_id():
    p = auth_storage_path("abc-123")
    assert p.name == "abc-123.json"
    assert p.parent.name == "auth"


def test_login_page_detection():
    assert looks_like_login_page("http://x/login", "Sign in", "http://x/login") is True
    assert looks_like_login_page("http://x/dashboard", "Welcome", "http://x/login") is False
    # password field present in text-ish signal
    assert looks_like_login_page("http://x/", "please Log in to continue", "http://x/login") is True


def test_build_login_script_embeds_values_and_is_runnable_source():
    src = build_login_script(
        {"login_url": "http://x/login", "username": "u", "password": "p",
         "selectors": {}, "success_check": "/home"},
        base_url="http://x", storage_path="/tmp/s.json", headless=True,
    )
    assert "u" in src and "http://x/login" in src and "/tmp/s.json" in src
    compile(src, "<login>", "exec")  # must be valid Python
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_playwright_login_helpers.py -v`
Expected: FAIL (module `backend.services.playwright_login` not found).

- [ ] **Step 3: Create `backend/services/playwright_login.py` (helpers only)**

```python
"""Helpers + session capture for authenticated auto-execute.

Credentials are injected only into the server-assembled login script that runs
in the sandboxed subprocess; they are never returned to clients or written into
generated test code.
"""

from __future__ import annotations

from pathlib import Path

from backend.config import get_settings

_LOGIN_WRAPPER = Path(__file__).with_name("playwright_login_wrapper.py.tmpl")


def _data_dir() -> Path:
    # DATABASE_PATH is like "data/testgen.db"; sessions live alongside it under data/auth/.
    db_path = Path(get_settings().database_path)
    return db_path.parent


def auth_storage_path(project_id: str) -> Path:
    return _data_dir() / "auth" / f"{project_id}.json"


def mask_auth_config(auth: dict) -> dict:
    masked = {k: v for k, v in (auth or {}).items() if k != "password"}
    masked["password_set"] = bool((auth or {}).get("password"))
    return masked


def looks_like_login_page(final_url: str, page_text: str, login_url: str) -> bool:
    if login_url and login_url.rstrip("/") == (final_url or "").rstrip("/"):
        return True
    text = (page_text or "").lower()
    return any(sig in text for sig in ("sign in", "log in", "login", "password"))


def build_login_script(auth: dict, base_url: str, storage_path: str, headless: bool) -> str:
    template = _LOGIN_WRAPPER.read_text(encoding="utf-8")
    sel = auth.get("selectors") or {}
    return template.format(
        login_url=auth.get("login_url", ""),
        username=auth.get("username", ""),
        password=auth.get("password", ""),
        sel_username=sel.get("username", ""),
        sel_password=sel.get("password", ""),
        sel_submit=sel.get("submit", ""),
        success_check=auth.get("success_check", ""),
        base_url=base_url,
        storage_path=storage_path,
        headless=headless,
    )
```

- [ ] **Step 4: Create the login wrapper template `backend/services/playwright_login_wrapper.py.tmpl`**

```python
"""Server-assembled login script. Formatted by build_login_script and run in the
sandboxed subprocess. Prints exactly one JSON object as its last action:
  {{"ok": bool, "screenshot_b64": str|null, "error": str|null}}
Uses only positional heuristics + optional selector overrides. No user/LLM code.
"""

import asyncio
import base64
import json
import sys

LOGIN_URL = {login_url!r}
USERNAME = {username!r}
PASSWORD = {password!r}
SEL_USERNAME = {sel_username!r}
SEL_PASSWORD = {sel_password!r}
SEL_SUBMIT = {sel_submit!r}
SUCCESS_CHECK = {success_check!r}
BASE_URL = {base_url!r}
STORAGE_PATH = {storage_path!r}
HEADLESS = {headless!r}


async def _shot(page):
    try:
        png = await page.screenshot(type="jpeg", quality=80, full_page=False)
        return base64.b64encode(png).decode("ascii")
    except Exception:
        return None


async def _fill_username(page):
    if SEL_USERNAME:
        await page.locator(SEL_USERNAME).first.fill(USERNAME)
        return
    for getter in (
        lambda: page.get_by_placeholder("Username"),
        lambda: page.get_by_placeholder("Email"),
        lambda: page.get_by_role("textbox", name="Username"),
        lambda: page.get_by_role("textbox", name="Email"),
        lambda: page.locator("input[type='email']"),
        lambda: page.locator("input[name='username'], input[name='email']"),
    ):
        loc = getter().first
        if await loc.count() > 0:
            await loc.fill(USERNAME)
            return
    raise RuntimeError("Could not find a username/email field")


async def _fill_password(page):
    loc = page.locator(SEL_PASSWORD).first if SEL_PASSWORD else page.locator("input[type='password']").first
    if await loc.count() == 0:
        raise RuntimeError("Could not find a password field")
    await loc.fill(PASSWORD)


async def _submit(page):
    if SEL_SUBMIT:
        await page.locator(SEL_SUBMIT).first.click()
        return
    for getter in (
        lambda: page.get_by_role("button", name="Sign in"),
        lambda: page.get_by_role("button", name="Log in"),
        lambda: page.get_by_role("button", name="Login"),
        lambda: page.locator("button[type='submit'], input[type='submit']"),
    ):
        loc = getter().first
        if await loc.count() > 0:
            await loc.click()
            return
    raise RuntimeError("Could not find a submit button")


async def main():
    from playwright.async_api import async_playwright
    result = {{"ok": False, "screenshot_b64": None, "error": None}}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(viewport={{"width": 1280, "height": 720}})
            page = await context.new_page()
            try:
                await page.goto(LOGIN_URL)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await _fill_username(page)
                await _fill_password(page)
                await _submit(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Verify login worked.
                ok = True
                if SUCCESS_CHECK:
                    if SUCCESS_CHECK.startswith("/"):
                        ok = SUCCESS_CHECK in page.url
                    else:
                        ok = await page.get_by_text(SUCCESS_CHECK).first.count() > 0
                else:
                    ok = page.url.rstrip("/") != LOGIN_URL.rstrip("/")
                result["screenshot_b64"] = await _shot(page)
                if not ok:
                    result["error"] = "Login did not reach the expected state (check credentials/selectors)."
                else:
                    await context.storage_state(path=STORAGE_PATH)
                    result["ok"] = True
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        result["error"] = f"{{type(e).__name__}}: {{e}}"
    finally:
        sys.stdout.write(json.dumps(result))
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_playwright_login_helpers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/services/playwright_login.py backend/services/playwright_login_wrapper.py.tmpl tests/services/test_playwright_login_helpers.py
git commit -m "feat: login helpers and login wrapper template"
```

---

### Task 3: Runner supports `storage_state`; add `capture_login_session`

**Files:**
- Modify: `backend/services/playwright_runner.py` (extract subprocess helper; add `storage_state_path` param)
- Modify: `backend/services/playwright_runner_wrapper.py.tmpl` (context uses storage_state)
- Modify: `backend/services/playwright_login.py` (add async `capture_login_session`)
- Test: `tests/services/test_auth_session_integration.py`

**Interfaces:**
- Consumes: `build_login_script`, `auth_storage_path`, `looks_like_login_page` (Task 2).
- Produces:
  - `run_playwright_code(code, base_url, headless, storage_state_path: str | None = None) -> dict` (added kwarg; default keeps current behavior)
  - `playwright_runner._run_script_subprocess(script: str) -> dict` — runs a formatted script, returns the parsed JSON dict (or an error dict)
  - `playwright_login.capture_login_session(auth: dict, base_url: str, project_id: str, headless: bool = True) -> dict` returning `{"ok": bool, "screenshot_b64": str|None, "error": str|None}`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/services/test_auth_session_integration.py
import http.server
import socketserver
import threading
import pytest
from pathlib import Path

from backend.services.playwright_login import capture_login_session, auth_storage_path
from backend.services.playwright_runner import run_playwright_code

LOGIN_HTML = """<!doctype html><form>
<input placeholder="Username" id="u"><input type="password" id="p">
<button type="submit" id="go">Sign in</button>
<script>
document.querySelector('#go').addEventListener('click', function(e){
  e.preventDefault();
  if (document.querySelector('#p').value === 'secret') {
    localStorage.setItem('authed','1'); location.href='/protected';
  } else { document.body.innerHTML += '<p>Invalid</p>'; }
});
</script></form>"""

PROTECTED_HTML = """<!doctype html><body><script>
if (localStorage.getItem('authed')==='1'){document.body.innerHTML='<h1>Welcome dashboard</h1>';}
else {location.href='/login';}
</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = LOGIN_HTML if self.path.startswith("/login") else PROTECTED_HTML
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


async def test_capture_then_authenticated_run(server, tmp_path, monkeypatch):
    pid = "testproj"
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "secret",
            "selectors": {}, "success_check": "/protected"}
    res = await capture_login_session(auth, base_url=server, project_id=pid)
    assert res["ok"] is True, res
    assert auth_storage_path(pid).exists()

    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/protected')\n"
            "    await page.get_by_text('Welcome dashboard').first.wait_for(state='visible', timeout=8000)\n"
            "    assert await page.get_by_text('Welcome dashboard').first.count() > 0, 'not authed'\n")
    run = await run_playwright_code(code, server, headless=True,
                                    storage_state_path=str(auth_storage_path(pid)))
    assert run["status"] == "passed", run


async def test_capture_wrong_password_fails_and_writes_nothing(server, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "WRONG",
            "selectors": {}, "success_check": "/protected"}
    res = await capture_login_session(auth, base_url=server, project_id="p2")
    assert res["ok"] is False
    assert not auth_storage_path("p2").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_auth_session_integration.py -v`
Expected: FAIL (`capture_login_session` undefined / `storage_state_path` unexpected kwarg). Requires `playwright install chromium` on the host.

- [ ] **Step 3: Extract a reusable subprocess helper in `playwright_runner.py`**

Replace the body of `run_playwright_code` that builds/runs the temp script (the `with tempfile.TemporaryDirectory(...)` block plus JSON parsing) by delegating to a new module-level helper. Add this function above `run_playwright_code`:

```python
def _run_script_blocking(script: str) -> dict:
    """Write `script` to a temp file, run it in a scrubbed subprocess, return the
    parsed JSON dict it prints (or a structured error dict)."""
    SECRET_KEY_PATTERNS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "JWT", "PRIVATE_KEY")
    env = {k: v for k, v in os.environ.items()
           if not any(p in k.upper() for p in SECRET_KEY_PATTERNS)}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="pw_run_") as tmpdir:
        script_path = Path(tmpdir) / "runner.py"
        script_path.write_text(script, encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(script_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, timeout=TIMEOUT_SECONDS)
            stdout, stderr, timed_out = proc.stdout or b"", proc.stderr or b"", False
        except subprocess.TimeoutExpired as e:
            stdout, stderr, timed_out = (e.stdout or b""), (e.stderr or b""), True
    if timed_out:
        return {"_timeout": True, "_stderr": stderr.decode("utf-8", errors="replace")}
    text = stdout.decode("utf-8", errors="replace").strip()
    last_line = text.rsplit("\n", 1)[-1] if text else "{}"
    try:
        return json.loads(last_line)
    except Exception as e:
        return {"_parse_error": str(e), "_stderr": stderr.decode("utf-8", errors="replace")[-2000:]}
```

Now rewrite `run_playwright_code` to use it and accept `storage_state_path`:

```python
async def run_playwright_code(code: str, base_url: str, headless: bool,
                              storage_state_path: str | None = None) -> dict:
    url_ok, url_err = _validate_url(base_url)
    if not url_ok:
        return {"status": "error", "screenshot_b64": None,
                "error_message": url_err, "console_log": "", "duration_ms": 0}
    bad = _check_denylist(code)
    if bad:
        return {"status": "error", "screenshot_b64": None,
                "error_message": f"Code failed safety check (blocked: {bad}).",
                "console_log": "", "duration_ms": 0}

    template = WRAPPER_PATH.read_text(encoding="utf-8")
    state = storage_state_path if (storage_state_path and Path(storage_state_path).exists()) else None
    script = template.format(user_code=code, base_url=base_url, headless=headless,
                             storage_state=state)

    result = await asyncio.to_thread(_run_script_blocking, script)
    if result.get("_timeout"):
        return {"status": "error", "screenshot_b64": None,
                "error_message": f"Timeout ({int(TIMEOUT_SECONDS)}s)",
                "console_log": "", "duration_ms": int(TIMEOUT_SECONDS * 1000)}
    if "_parse_error" in result or "status" not in result:
        return {"status": "error", "screenshot_b64": None,
                "error_message": f"Runner produced unparsable output: {result.get('_parse_error','no status')}",
                "console_log": result.get("_stderr", ""), "duration_ms": 0}
    return result
```

- [ ] **Step 4: Update the test wrapper template for `storage_state`**

In `backend/services/playwright_runner_wrapper.py.tmpl`, add the param and use it. After `HEADLESS = {headless!r}` add:

```python
STORAGE_STATE = {storage_state!r}
```

Change the context creation line from:
```python
            context = await browser.new_context(viewport={{"width": 1280, "height": 720}})
```
to:
```python
            context = await browser.new_context(
                viewport={{"width": 1280, "height": 720}},
                storage_state=STORAGE_STATE,
            )
```

(`new_context(storage_state=None)` is valid and equivalent to no state.)

- [ ] **Step 5: Add `capture_login_session` to `playwright_login.py`**

```python
async def capture_login_session(auth: dict, base_url: str, project_id: str,
                                headless: bool = True) -> dict:
    """Run the server-assembled login script in the sandbox and persist storage_state.

    Returns {"ok": bool, "screenshot_b64": str|None, "error": str|None}.
    """
    import asyncio as _asyncio
    from backend.services.playwright_runner import _run_script_blocking, _validate_url

    ok_url, err = _validate_url(auth.get("login_url") or "")
    if not ok_url:
        return {"ok": False, "screenshot_b64": None, "error": f"Login URL invalid: {err}"}

    path = auth_storage_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = build_login_script(auth, base_url, str(path), headless)
    result = await _asyncio.to_thread(_run_script_blocking, script)
    if result.get("_timeout"):
        return {"ok": False, "screenshot_b64": None, "error": "Login timed out (60s)."}
    if "ok" not in result:
        return {"ok": False, "screenshot_b64": None,
                "error": f"Login runner error: {result.get('_stderr') or result.get('_parse_error') or 'unknown'}"}
    return {"ok": bool(result.get("ok")), "screenshot_b64": result.get("screenshot_b64"),
            "error": result.get("error")}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_auth_session_integration.py -v`
Expected: PASS (both). (Ensure `playwright install chromium` was run.)

- [ ] **Step 7: Run the full suite (no regressions in existing runner tests)**

Run: `python -m pytest -q`
Expected: all pass (existing `tests/audit/test_playwright_runner.py` still green — signature change is backward-compatible).

- [ ] **Step 8: Commit**

```bash
git add backend/services/playwright_runner.py backend/services/playwright_runner_wrapper.py.tmpl backend/services/playwright_login.py tests/services/test_auth_session_integration.py
git commit -m "feat: capture and reuse authenticated storage_state in the runner"
```

---

### Task 4: API — save auth config, verify/capture session, mask on read

**Files:**
- Modify: `backend/models/requests.py` (add `SaveAuthBody`)
- Modify: `backend/routers/projects.py` (mask `auth_config` in `get_project`; add `PUT /{id}/auth`, `POST /{id}/auth/verify`)
- Test: `tests/routers/test_auth_endpoints.py`

**Interfaces:**
- Consumes: `project_repo.get_project`, `update_project_auth`, `get_project_auth` (Task 1); `mask_auth_config`, `capture_login_session` (Tasks 2–3).
- Produces:
  - `SaveAuthBody(login_url: str, username: str, password: str | None = None, selectors: dict = {}, success_check: str = "")`
  - `PUT /api/projects/{id}/auth` → returns `{"auth_config": <masked>}`
  - `POST /api/projects/{id}/auth/verify` → `{"ok": bool, "screenshot_b64": str|None, "error": str|None}`
  - `GET /api/projects/{id}` → `project.auth_config` is **masked**

- [ ] **Step 1: Write the failing test**

```python
# tests/routers/test_auth_endpoints.py
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def _new_project():
    r = client.post("/api/projects", json={"name": "P", "description": ""})
    return r.json()["id"]


def test_put_auth_masks_password_and_get_is_masked():
    pid = _new_project()
    r = client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login", "username": "bob", "password": "s3cret"})
    assert r.status_code == 200
    body = r.json()["auth_config"]
    assert "password" not in body and body["password_set"] is True
    assert body["username"] == "bob"
    # GET project also masked
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert "password" not in got and got["password_set"] is True


def test_put_auth_without_password_keeps_existing():
    pid = _new_project()
    client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login", "username": "bob", "password": "s3cret"})
    # second save omits password -> keep the stored one
    client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login2", "username": "bob"})
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert got["password_set"] is True
    assert got["login_url"] == "http://x/login2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/routers/test_auth_endpoints.py -v`
Expected: FAIL (404 — routes not defined / password not masked).

- [ ] **Step 3: Add `SaveAuthBody` to `backend/models/requests.py`**

```python
class SaveAuthBody(BaseModel):
    login_url: str
    username: str
    password: str | None = None  # omitted = keep the existing stored password
    selectors: dict[str, str] = {}
    success_check: str = ""
```

(Add `dict` to typing usage; `BaseModel` already imported.)

- [ ] **Step 4: Mask `auth_config` in `get_project` and add the two routes in `projects.py`**

Add imports near the top:
```python
from backend.models.requests import SaveAuthBody  # add to the existing requests import block
from backend.services.playwright_login import mask_auth_config, capture_login_session
```

In `get_project`, before building the response, mask the config on the model:
```python
        p.auth_config = mask_auth_config(p.auth_config)
```

Append the routes (after `update_context`):
```python
@router.put("/{project_id}/auth")
async def save_project_auth(
    project_id: str,
    body: SaveAuthBody,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    async with get_db() as db:
        existing = await project_repo.get_project_auth(db, user_id, project_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Project not found")
        password = body.password if body.password else existing.get("password", "")
        cfg = {
            "login_url": body.login_url.strip(),
            "username": body.username,
            "password": password,
            "selectors": body.selectors or {},
            "success_check": body.success_check or "",
            "verified_at": existing.get("verified_at", ""),
            "last_error": existing.get("last_error", ""),
        }
        await project_repo.update_project_auth(db, user_id, project_id, cfg)
    return {"auth_config": mask_auth_config(cfg)}


@router.post("/{project_id}/auth/verify")
async def verify_project_auth(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    from datetime import datetime, timezone
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        auth = await project_repo.get_project_auth(db, user_id, project_id)
    if not auth or not auth.get("login_url") or not auth.get("password"):
        raise HTTPException(status_code=400, detail="Set login URL, username, and password first.")
    base_url = (proj.base_url or "").strip().rstrip("/")
    res = await capture_login_session(auth, base_url, project_id)
    auth["verified_at"] = datetime.now(timezone.utc).isoformat() if res["ok"] else auth.get("verified_at", "")
    auth["last_error"] = "" if res["ok"] else (res.get("error") or "Login failed")
    async with get_db() as db:
        await project_repo.update_project_auth(db, user_id, project_id, auth)
    return res
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/routers/test_auth_endpoints.py -v`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add backend/models/requests.py backend/routers/projects.py tests/routers/test_auth_endpoints.py
git commit -m "feat: auth-config API with masked reads and session verify"
```

---

### Task 5: Auto-execute run uses the session + one auto-relogin retry

**Files:**
- Modify: `backend/routers/playwright_exec.py` (`run_playwright` resolves storage_state + retry)
- Test: `tests/routers/test_run_uses_session.py`

**Interfaces:**
- Consumes: `auth_storage_path`, `capture_login_session`, `looks_like_login_page` (Tasks 2–3); `project_repo.get_project_auth`.
- Produces: `run-playwright` behavior — passes the project's session file to `run_playwright_code`; on a result whose `error_message`/page context looks like a login page and auth is configured, re-captures the session once and retries.

- [ ] **Step 1: Write the failing test**

```python
# tests/routers/test_run_uses_session.py
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def _project_with_tc():
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    return pid, fid


@patch("backend.routers.playwright_exec.run_playwright_code", new_callable=AsyncMock)
def test_run_passes_storage_state_when_session_exists(mock_run, tmp_path, monkeypatch):
    from backend.services import playwright_login
    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)
    pid, fid = _project_with_tc()
    # create a test case row via direct repo insert is heavy; use generate is LLM.
    # Instead seed via the DB helper the suite already exposes:
    import backend.tests_util as tu  # if present; otherwise use conftest factory
    tcid = tu.seed_test_case(pid, fid)
    # session file present
    p = playwright_login.auth_storage_path(pid); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("{}")
    mock_run.return_value = {"status": "passed", "screenshot_b64": None,
                             "error_message": None, "console_log": "", "duration_ms": 1}
    r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
                    json={"code": "async def test(page, base_url):\n    pass\n", "headless": True})
    assert r.status_code == 200
    # storage_state_path kwarg was forwarded
    _, kwargs = mock_run.call_args
    assert kwargs.get("storage_state_path", "").endswith(f"{pid}.json")
```

> Note: if the suite has no `seed_test_case` helper, add a small factory in `tests/conftest.py` that inserts a `test_cases` row directly (mirror the columns in `backend/db.py`) and returns its id. Keep it in conftest so other tests can reuse it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/routers/test_run_uses_session.py -v`
Expected: FAIL (kwarg not forwarded).

- [ ] **Step 3: Wire session + retry into `run_playwright` in `playwright_exec.py`**

Add imports:
```python
from backend.services.playwright_login import auth_storage_path, capture_login_session, looks_like_login_page
```

Inside `run_playwright`, after loading `proj`/`tc` and computing `base_url`, resolve the session and pass it, then retry once on apparent auth expiry:

```python
    state_path = auth_storage_path(project_id)
    state_arg = str(state_path) if state_path.exists() else None

    async def _run():
        return await run_playwright_code(body.code, base_url, body.headless, storage_state_path=state_arg)

    result = await _run()

    # One auto-relogin + retry if the run looks like it hit a login wall.
    auth = None
    async with get_db() as db:
        auth = await project_repo.get_project_auth(db, user_id, project_id)
    if auth and auth.get("login_url") and auth.get("password"):
        msg = (result.get("error_message") or "")
        if result.get("status") != "passed" and looks_like_login_page(base_url, msg, auth["login_url"]):
            cap = await capture_login_session(auth, base_url, project_id)
            if cap.get("ok"):
                state_arg = str(state_path)
                result = await _run()
```

Replace the previous single `run_playwright_code(...)` call with the block above. Keep the existing persistence (`record_test_run`, `save_playwright_code`) as-is after it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/routers/test_run_uses_session.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/playwright_exec.py tests/routers/test_run_uses_session.py tests/conftest.py
git commit -m "feat: auto-execute runs authenticated with one relogin retry"
```

---

### Task 6: Frontend — "Login setup" card in Project Overview

**Files:**
- Modify: `frontend/index.html` (add the Login setup card after the Base URL card, ~line 497)
- Modify: `frontend/app.js` (render/save/verify logic; wire into `loadProjectWorkspaceData`)
- (No test — manual verification step included)

**Interfaces:**
- Consumes: `PUT /api/projects/{id}/auth`, `POST /api/projects/{id}/auth/verify`, and the masked `auth_config` on `GET /api/projects/{id}` (Task 4).

- [ ] **Step 1: Add the card markup in `index.html`**

Insert immediately after the Base URL card's closing `</div>` (before the Project Description card, ~line 497):

```html
        <!-- Login setup card -->
        <div class="rounded-xl p-4 shadow-sm" style="background:var(--bg-surface);border:1px solid var(--border-default);">
          <div class="flex justify-between items-center gap-2 mb-2">
            <h2 class="text-base font-semibold">Login setup</h2>
            <span id="authStatus" class="text-xs" style="color:var(--text-tertiary);">Not set</span>
          </div>
          <p class="text-xs mb-3" style="color:var(--text-tertiary);">Credentials for authenticated auto-execute. Stored in the database, masked, never in generated code.</p>
          <div class="grid gap-2 sm:grid-cols-2">
            <input id="authLoginUrl" type="url" placeholder="Login URL (e.g. https://app/login)" class="text-sm rounded px-3 py-2" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
            <input id="authUsername" type="text" placeholder="Username / email" class="text-sm rounded px-3 py-2" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
            <input id="authPassword" type="password" placeholder="Password (leave blank to keep)" class="text-sm rounded px-3 py-2" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
            <input id="authSuccessCheck" type="text" placeholder="Success check (path or text, optional)" class="text-sm rounded px-3 py-2" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
          </div>
          <details class="mt-2">
            <summary class="text-xs cursor-pointer" style="color:var(--text-tertiary);">Advanced — selector overrides</summary>
            <div class="grid gap-2 sm:grid-cols-3 mt-2">
              <input id="authSelUser" type="text" placeholder="Username selector" class="text-xs rounded px-2 py-1.5" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
              <input id="authSelPass" type="text" placeholder="Password selector" class="text-xs rounded px-2 py-1.5" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
              <input id="authSelSubmit" type="text" placeholder="Submit selector" class="text-xs rounded px-2 py-1.5" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
            </div>
          </details>
          <div class="flex items-center gap-2 mt-3">
            <button type="button" id="btnSaveAuth" class="text-sm px-3 py-1.5 rounded font-medium" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Save</button>
            <button type="button" id="btnVerifyAuth" class="text-sm px-3 py-1.5 rounded font-medium" style="background:var(--accent);color:var(--text-on-accent);">Test login &amp; save session</button>
            <span id="authError" class="text-xs" style="color:var(--status-high);"></span>
          </div>
          <img id="authShot" class="hidden mt-3 rounded border max-w-full" style="border-color:var(--border-default);" alt="login result" />
        </div>
```

- [ ] **Step 2: Populate + wire the card in `app.js`**

In the function that loads project data (where `baseUrlSection?.setValue(p.base_url ...)` is called, ~line 541), add:

```javascript
  renderAuthConfig(p.auth_config || {});
```

Add these functions near the base-URL helpers (~line 900):

```javascript
function renderAuthConfig(cfg) {
  el("authLoginUrl").value = cfg.login_url || "";
  el("authUsername").value = cfg.username || "";
  el("authPassword").value = "";
  el("authSuccessCheck").value = cfg.success_check || "";
  const sel = cfg.selectors || {};
  el("authSelUser").value = sel.username || "";
  el("authSelPass").value = sel.password || "";
  el("authSelSubmit").value = sel.submit || "";
  const status = el("authStatus");
  if (cfg.last_error) status.textContent = "Last attempt failed";
  else if (cfg.verified_at) status.textContent = "Session saved · verified";
  else if (cfg.password_set) status.textContent = "Credentials set — not verified";
  else status.textContent = "Not set";
}

function _authBody() {
  const pw = el("authPassword").value;
  const body = {
    login_url: el("authLoginUrl").value.trim(),
    username: el("authUsername").value.trim(),
    success_check: el("authSuccessCheck").value.trim(),
    selectors: {
      username: el("authSelUser").value.trim(),
      password: el("authSelPass").value.trim(),
      submit: el("authSelSubmit").value.trim(),
    },
  };
  if (pw) body.password = pw;
  return body;
}

async function saveAuthConfig() {
  el("authError").textContent = "";
  const r = await fetchJSON(`/api/projects/${currentProjectId}/auth`, {
    method: "PUT", body: JSON.stringify(_authBody()),
  });
  renderAuthConfig(r.auth_config || {});
  showToast("Login settings saved.");
}

async function verifyAuthConfig() {
  el("authError").textContent = "";
  el("authShot").classList.add("hidden");
  const btn = el("btnVerifyAuth");
  btn.disabled = true; btn.textContent = "Testing…";
  try {
    await fetchJSON(`/api/projects/${currentProjectId}/auth`, { method: "PUT", body: JSON.stringify(_authBody()) });
    const res = await fetchJSON(`/api/projects/${currentProjectId}/auth/verify`, { method: "POST", body: "{}" });
    if (res.ok) {
      el("authStatus").textContent = "Session saved · verified";
      showToast("Login succeeded — session saved.");
    } else {
      el("authStatus").textContent = "Last attempt failed";
      el("authError").textContent = res.error || "Login failed";
    }
    if (res.screenshot_b64) {
      const img = el("authShot"); img.src = "data:image/jpeg;base64," + res.screenshot_b64;
      img.classList.remove("hidden");
    }
  } catch (e) {
    el("authError").textContent = String(e.message || e);
  } finally {
    btn.disabled = false; btn.textContent = "Test login & save session";
  }
}
```

Wire the buttons alongside the other listeners (near the base-URL button wiring, ~line 911):

```javascript
el("btnSaveAuth")?.addEventListener("click", () => saveAuthConfig().catch(e => showToast(String(e.message || e), true)));
el("btnVerifyAuth")?.addEventListener("click", () => verifyAuthConfig());
```

- [ ] **Step 3: Manual verification (drive the real app)**

```bash
source .venv/bin/activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080   # (run in background)
```
Then, against a public login demo (e.g. `https://www.saucedemo.com`, base URL `https://www.saucedemo.com`, login URL same, user `standard_user`, password `secret_sauce`, success check `/inventory`):
1. Open a project → Project Overview → **Login setup**, fill fields, click **Test login & save session**. Expect "Session saved · verified" + a screenshot of the logged-in page.
2. Open a test case → **Auto-execute** → **Run**. Expect the test to run against the authenticated page.

Confirm `data/auth/<project_id>.json` exists and is gitignored (`git status` shows nothing under `data/`).

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: Login setup card for authenticated auto-execute"
```

---

### Task 7: Docs — document the feature and update limitations

**Files:**
- Modify: `docs/auto-execute.md` (add an "Authenticated runs" section)
- Modify: `docs/limitations.md` (mark fix #1 as shipped; keep the at-rest-encryption caveat)

- [ ] **Step 1: Add an "Authenticated runs" section to `docs/auto-execute.md`**

```markdown
## Authenticated runs (login)

Set up login once per project in **Project Overview → Login setup**: login URL,
username, password, and (optionally) selector overrides and a success check. Click
**Test login & save session** — the app logs in inside the sandbox and saves the
session (`data/auth/<project_id>.json`, gitignored). Every auto-execute run then starts
authenticated; if a run detects an expired session it re-logs-in once and retries.

Credentials are stored in SQLite (masked in the UI, never in git, never in generated
code). v1 supports a single login form (no MFA/SSO/OAuth) and Chromium only.
```

- [ ] **Step 2: Update `docs/limitations.md`**

Change the "Login / auth is out of scope" bullet under **Auto-execute** to note it is now supported via project Login setup, and under **Proposed fixes** mark **#1** as ✅ shipped, leaving the "credentials not encrypted at rest" caveat.

- [ ] **Step 3: Commit**

```bash
git add docs/auto-execute.md docs/limitations.md
git commit -m "docs: document authenticated auto-execute"
```

---

## Self-review notes

- **Spec coverage:** data model (T1) · session capture/storage_state (T2–T3) · runner reuse (T3) · auto-relogin (T5) · API save/verify + masking (T4) · UI card (T6) · security posture (T1–T4, denylist exemption in T3) · testing (unit T1–T2, integration T3, router T4–T5, manual T6) · docs (T7). All spec sections map to a task.
- **Auth-config shape** (`login_url, username, password, selectors{username,password,submit}, success_check, verified_at, last_error`) is consistent across T1/T2/T4/T6.
- **Signatures** consistent: `run_playwright_code(..., storage_state_path=None)`, `_run_script_blocking(script)->dict`, `capture_login_session(auth, base_url, project_id, headless=True)->{ok,screenshot_b64,error}`, `auth_storage_path(project_id)->Path`, `mask_auth_config(auth)->dict`, `looks_like_login_page(final_url,page_text,login_url)->bool`.
- **`_data_dir` seam** is monkeypatched in tests so sessions write to a tmp dir, not real `data/`.
```
