# Auto-execute Generation Improvements (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Generated tests navigate to the app's real authenticated landing page (not `/`), and login-flow tests run logged-out using the configured credentials injected at run time — with credentials still never in stored test code.

**Architecture:** Two pure helpers (`resolve_landing_path`, `is_login_test`) drive both generation (code shape + nav target) and execution (auth mode). The runner wrapper inspects the test function's arity and injects `username`/`password` for 4-arg login tests. Credentials are substituted server-side into the ephemeral wrapper only.

**Tech Stack:** Python 3.11, FastAPI, Playwright (async, Chromium), pytest (`asyncio_mode=auto`), vanilla-JS SPA.

## Global Constraints

- Credentials: SQLite only; never in `.env`/git/LLM prompts/**stored test code**; masked in every API response. Injection is at run time into the ephemeral wrapper.
- The user-code denylist + env-scrub + 60s timeout stay intact; the login wrapper is the only trusted denylist-exempt script.
- No schema change — `auth_config` gains an optional `home_path` JSON key.
- Backward compatibility: existing stored `async def test(page, base_url)` code must still run unchanged.
- Chromium only. No `Co-Authored-By` trailers. Run `python -m pytest -q` green before each backend commit. Use the project venv (`source .venv/bin/activate`).

---

### Task 1: Pure helpers `resolve_landing_path` and `is_login_test`

**Files:**
- Modify: `backend/services/playwright_login.py` (append two functions)
- Test: `tests/services/test_generation_helpers.py`

**Interfaces:**
- Produces:
  - `resolve_landing_path(auth_config: dict) -> str` — `home_path` if truthy; else `success_check` if it starts with `/`; else `""`.
  - `is_login_test(title: str, steps: list[str]) -> bool` — True if any login cue appears in the lowercased title or any step.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_generation_helpers.py
from backend.services.playwright_login import resolve_landing_path, is_login_test


def test_resolve_landing_path_precedence():
    assert resolve_landing_path({"home_path": "/dash", "success_check": "/inv"}) == "/dash"
    assert resolve_landing_path({"success_check": "/inventory.html"}) == "/inventory.html"
    assert resolve_landing_path({"success_check": "Products"}) == ""
    assert resolve_landing_path({}) == ""


def test_is_login_test_cues():
    assert is_login_test("Verify valid login grants access", []) is True
    assert is_login_test("Sign in with correct credentials", []) is True
    assert is_login_test("Check homepage", ["User clicks Log in link"]) is True
    assert is_login_test("Add item to cart", ["Click Add to cart"]) is False
    assert is_login_test("Verify navigation menu opens", []) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_generation_helpers.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement the helpers**

Append to `backend/services/playwright_login.py`:

```python
_LOGIN_CUES = ("sign in", "sign-in", "signin", "log in", "log-in", "login")


def resolve_landing_path(auth_config: dict) -> str:
    """Path an authenticated test should open, or '' to mean '/'."""
    cfg = auth_config or {}
    home = (cfg.get("home_path") or "").strip()
    if home:
        return home
    success = (cfg.get("success_check") or "").strip()
    if success.startswith("/"):
        return success
    return ""


def is_login_test(title: str, steps: list[str]) -> bool:
    """Heuristic: does this test exercise a login/sign-in flow?"""
    haystack = (title or "").lower()
    for s in steps or []:
        haystack += "\n" + str(s).lower()
    return any(cue in haystack for cue in _LOGIN_CUES)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_generation_helpers.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/services/playwright_login.py tests/services/test_generation_helpers.py
git commit -m "feat: landing-path and login-test detection helpers"
```

---

### Task 2: Runner injects credentials via arity dispatch

**Files:**
- Modify: `backend/services/playwright_runner.py` (`run_playwright_code` signature + template.format args)
- Modify: `backend/services/playwright_runner_wrapper.py.tmpl` (USERNAME/PASSWORD + inspect arity)
- Test: `tests/services/test_credential_injection.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_playwright_code(code, base_url, headless, storage_state_path=None, username="", password="") -> dict` — 4-arg `test` functions receive `(page, base_url, username, password)`; 2-arg receive `(page, base_url)` as before.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/services/test_credential_injection.py
import http.server, socketserver, threading
import pytest
from backend.services.playwright_runner import run_playwright_code

PAGE = """<!doctype html><body><input placeholder="Username" id="u"><input type="password" id="p">
<button id="go">Sign in</button><script>
document.querySelector('#go').addEventListener('click',function(){
 document.body.innerHTML += '<div id="echo">'+document.querySelector('#u').value+'|'+document.querySelector('#p').value+'</div>';
});</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers()
        self.wfile.write(PAGE.encode())
    def log_message(self,*a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


async def test_four_arg_test_receives_injected_credentials(server):
    code = (
        "async def test(page, base_url, username, password):\n"
        "    await page.goto(base_url + '/')\n"
        "    await page.get_by_placeholder('Username').fill(username)\n"
        "    await page.locator(\"input[type='password']\").fill(password)\n"
        "    await page.locator('#go').click()\n"
        "    await page.locator('#echo').wait_for(state='visible', timeout=8000)\n"
        "    txt = await page.locator('#echo').inner_text()\n"
        "    assert txt == 'alice|s3cret', txt\n"
    )
    res = await run_playwright_code(code, server, headless=True, username="alice", password="s3cret")
    assert res["status"] == "passed", res


async def test_two_arg_test_still_runs(server):
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_placeholder('Username').count() == 1\n")
    res = await run_playwright_code(code, server, headless=True, username="x", password="y")
    assert res["status"] == "passed", res
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_credential_injection.py -v`
Expected: FAIL (`run_playwright_code` has no `username` kwarg).

- [ ] **Step 3: Extend `run_playwright_code`**

In `backend/services/playwright_runner.py`, change the signature and the `template.format(...)` call:

```python
async def run_playwright_code(code: str, base_url: str, headless: bool,
                              storage_state_path: str | None = None,
                              username: str = "", password: str = "") -> dict:
```

And where it builds the script (the `template.format(...)` line), add the two fields:

```python
    script = template.format(user_code=code, base_url=base_url, headless=headless,
                             storage_state=state, username=username, password=password)
```

(Leave the denylist/validation/`_run_script_blocking` flow unchanged.)

- [ ] **Step 4: Update the wrapper template for arity dispatch**

In `backend/services/playwright_runner_wrapper.py.tmpl`:

Add `import inspect` to the imports block. After `STORAGE_STATE = {storage_state!r}` add:

```python
USERNAME = {username!r}
PASSWORD = {password!r}
```

Replace the call site:
```python
                        await test_fn(page, BASE_URL)
```
with:
```python
                        if len(inspect.signature(test_fn).parameters) >= 4:
                            await test_fn(page, BASE_URL, USERNAME, PASSWORD)
                        else:
                            await test_fn(page, BASE_URL)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_credential_injection.py -v`
Expected: PASS (both). Then `python -m pytest tests/audit/test_playwright_runner.py -q` — existing runner tests still pass (backward-compatible).

- [ ] **Step 6: Commit**

```bash
git add backend/services/playwright_runner.py backend/services/playwright_runner_wrapper.py.tmpl tests/services/test_credential_injection.py
git commit -m "feat: inject credentials into 4-arg auto-execute tests"
```

---

### Task 3: Generator prompt — login mode + landing path

**Files:**
- Modify: `backend/prompts/templates.py` (`PLAYWRIGHT_SYSTEM_PROMPT` signature line; `build_playwright_user_message`)
- Modify: `backend/services/llm_service.py` (`generate_playwright_code` passes new args through)
- Test: `tests/services/test_playwright_prompt.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `build_playwright_user_message(tc: dict, base_url: str, *, is_login: bool = False, landing_path: str = "", has_credentials: bool = False) -> str`
  - `generate_playwright_code(tc_dict, base_url, settings, provider_override=None, model_override=None, *, is_login=False, landing_path="", has_credentials=False) -> str` (new keyword-only args, threaded into `build_playwright_user_message`)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_playwright_prompt.py
from backend.prompts.templates import build_playwright_user_message

TC = {"title": "Verify valid login", "preconditions": "", "steps": ["Enter user", "Enter pass", "Click Login"], "expected_result": "Lands on dashboard"}


def test_login_mode_prompt_uses_credential_params():
    msg = build_playwright_user_message(TC, "http://x", is_login=True, has_credentials=True)
    assert "async def test(page, base_url, username, password)" in msg
    assert "username" in msg and "password" in msg


def test_non_login_mode_uses_landing_path():
    msg = build_playwright_user_message(
        {"title": "Nav menu", "preconditions": "", "steps": [], "expected_result": "menu shows"},
        "http://x", is_login=False, landing_path="/inventory.html")
    assert "/inventory.html" in msg
    assert "async def test(page, base_url)" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_playwright_prompt.py -v`
Expected: FAIL (`build_playwright_user_message` has no `is_login` kwarg).

- [ ] **Step 3: Update the system prompt signature line**

In `backend/prompts/templates.py`, in `PLAYWRIGHT_SYSTEM_PROMPT`, replace the line:
```
    "    async def test(page, base_url):\n"
```
with:
```
    "    async def test(page, base_url):\n"
    "  or, for login-flow tests, exactly:\n"
    "    async def test(page, base_url, username, password):\n"
```
and change "just the function definition." to "just the function definition with the signature the request specifies."

- [ ] **Step 4: Rewrite `build_playwright_user_message`**

Replace the function with:

```python
def build_playwright_user_message(tc: dict, base_url: str, *, is_login: bool = False,
                                  landing_path: str = "", has_credentials: bool = False) -> str:
    steps_section = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(tc.get('steps') or []))
    header = (
        "Generate a Playwright Python async test for the following manual test case.\n\n"
        f"BASE_URL: {base_url}\n\n"
    )
    body = (
        f"Title: {tc.get('title', '')}\n"
        f"Preconditions: {tc.get('preconditions', '')}\n"
        f"Steps:\n{steps_section}\n"
        f"Expected result: {tc.get('expected_result', '')}\n\n"
    )
    if is_login and has_credentials:
        directive = (
            "This is a LOGIN test. Use EXACTLY this signature:\n"
            "    async def test(page, base_url, username, password):\n"
            "Navigate to the login page with `await page.goto(base_url + '/')` (or the login path "
            "implied by the steps). Fill the username/email field with the `username` parameter and "
            "the password field with the `password` parameter — NEVER hard-code credential values. "
            "Submit, then assert the post-login state.\n"
            "Output ONLY the `async def test(page, base_url, username, password):` function and its body."
        )
    else:
        target = f"base_url + '{landing_path}'" if landing_path else "base_url + '/'"
        directive = (
            "Use EXACTLY this signature:\n"
            "    async def test(page, base_url):\n"
            f"Start with `await page.goto({target})` — this is the page under test. "
            "Then perform the steps and assert the expected result.\n"
            "Output ONLY the `async def test(page, base_url):` function and its body."
        )
    return header + body + directive
```

- [ ] **Step 5: Thread the args through `generate_playwright_code`**

In `backend/services/llm_service.py`, change the signature to add keyword-only args and pass them to the builder:

```python
async def generate_playwright_code(
    tc_dict: dict,
    base_url: str,
    settings: Settings,
    provider_override: str | None = None,
    model_override: str | None = None,
    *,
    is_login: bool = False,
    landing_path: str = "",
    has_credentials: bool = False,
) -> str:
```
and change the `build_playwright_user_message(tc_dict, base_url)` call to:
```python
    user = build_playwright_user_message(
        tc_dict, base_url, is_login=is_login, landing_path=landing_path, has_credentials=has_credentials
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_playwright_prompt.py -v`
Expected: PASS (2 tests). Then `python -m pytest -q` (full suite green).

- [ ] **Step 7: Commit**

```bash
git add backend/prompts/templates.py backend/services/llm_service.py tests/services/test_playwright_prompt.py
git commit -m "feat: login-mode and landing-path aware Playwright prompt"
```

---

### Task 4: Wire endpoints (generate + run + auth home_path)

**Files:**
- Modify: `backend/models/requests.py` (`SaveAuthBody` + `home_path`)
- Modify: `backend/routers/playwright_exec.py` (`GenerateBody.login_mode`, `RunBody.logged_out`, generate + run wiring)
- Modify: `backend/routers/projects.py` (persist `home_path` in `save_project_auth`)
- Test: `tests/routers/test_generation_wiring.py`

**Interfaces:**
- Consumes: `is_login_test`, `resolve_landing_path` (Task 1); `run_playwright_code(..., username, password)` (Task 2); `generate_playwright_code(..., is_login, landing_path, has_credentials)` (Task 3); `project_repo.get_project_auth`.
- Produces:
  - `GenerateBody { regenerate: bool = False, login_mode: bool | None = None }`
  - `RunBody { code: str, headless: bool = True, logged_out: bool = False }`
  - `SaveAuthBody` gains `home_path: str = ""`; `save_project_auth` stores it.

- [ ] **Step 1: Write the failing test**

```python
# tests/routers/test_generation_wiring.py
from unittest.mock import AsyncMock, patch


def test_run_forwards_logged_out_and_creds_for_login_test(client, seed_test_case, monkeypatch, tmp_path):
    from backend.services import playwright_login
    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    client.put(f"/api/projects/{pid}/auth", json={"login_url": "http://127.0.0.1:1/login", "username": "u", "password": "p"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify valid login grants access")
    # session file exists but login test must run logged OUT (state None) with creds
    p = playwright_login.auth_storage_path(pid); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("{}")
    with patch("backend.routers.playwright_exec.run_playwright_code", new_callable=AsyncMock) as m:
        m.return_value = {"status": "passed", "screenshot_b64": None, "error_message": None, "console_log": "", "duration_ms": 1}
        client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright", json={"code": "async def test(page, base_url, username, password):\n    pass\n"})
        _, kw = m.call_args
        assert kw.get("storage_state_path") is None      # logged-out for a login test
        assert kw.get("username") == "u" and kw.get("password") == "p"


def test_save_auth_persists_home_path(client):
    pid = client.post("/api/projects", json={"name": "P2", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}/auth", json={"login_url": "http://x/login", "username": "u", "password": "p", "home_path": "/inventory.html"})
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert got["home_path"] == "/inventory.html"
```

(Uses the `client` + `seed_test_case` fixtures already in `tests/routers/conftest.py`. Extend `seed_test_case` to accept a `title=` kwarg if it doesn't already.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/routers/test_generation_wiring.py -v`
Expected: FAIL (home_path not persisted / creds+logged_out not forwarded).

- [ ] **Step 3: Add `home_path` to `SaveAuthBody`**

In `backend/models/requests.py`, add to `SaveAuthBody`:
```python
    home_path: str = ""
```

- [ ] **Step 4: Persist `home_path` in `save_project_auth`**

In `backend/routers/projects.py`, in `save_project_auth`, add to the `cfg` dict:
```python
            "home_path": (body.home_path or existing.get("home_path", "")).strip(),
```

- [ ] **Step 5: Wire generate + run in `playwright_exec.py`**

Add imports:
```python
from backend.services.playwright_login import is_login_test, resolve_landing_path
```

Add `login_mode` to `GenerateBody`:
```python
class GenerateBody(BaseModel):
    regenerate: bool = False
    login_mode: bool | None = None
```

Add `logged_out` to `RunBody`:
```python
class RunBody(BaseModel):
    code: str
    headless: bool = True
    logged_out: bool = False
```

In `generate_playwright`, after loading `tc` and before generating, compute mode + landing path and pass them:
```python
    async with get_db() as db:
        auth = await project_repo.get_project_auth(db, user_id, project_id) or {}
    is_login = body.login_mode if body.login_mode is not None else is_login_test(tc.title, tc.steps)
    landing_path = resolve_landing_path(auth)
    has_creds = bool(auth.get("username") and auth.get("password"))
```
and change the generate call to:
```python
        code = await generate_playwright_code(
            tc_dict, base_url, settings,
            is_login=is_login, landing_path=landing_path, has_credentials=has_creds,
        )
```

In `run_playwright`, replace the session/creds resolution:
```python
    async with get_db() as db:
        auth = await project_repo.get_project_auth(db, user_id, project_id) or {}
    logged_out = body.logged_out or is_login_test(tc.title, tc.steps)
    state_path = auth_storage_path(project_id)
    state_arg = None if logged_out else (str(state_path) if state_path.exists() else None)
    username = auth.get("username", "")
    password = auth.get("password", "")
```
and update the `_run()` helper to forward creds:
```python
            return await run_playwright_code(body.code, base_url, body.headless,
                                             storage_state_path=state_arg,
                                             username=username, password=password)
```
Keep the existing auto-relogin block, but guard it so it does NOT fire for a `logged_out` (login) run:
```python
    if not logged_out and auth and auth.get("login_url") and auth.get("password"):
        ...existing relogin/retry...
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/routers/test_generation_wiring.py -v`
Expected: PASS (both). Then `python -m pytest -q`.

- [ ] **Step 7: Commit**

```bash
git add backend/models/requests.py backend/routers/playwright_exec.py backend/routers/projects.py tests/routers/test_generation_wiring.py tests/routers/conftest.py
git commit -m "feat: wire login-mode, landing-path, logged-out run, and home_path"
```

---

### Task 5: Clearer verify error message

**Files:**
- Modify: `backend/services/playwright_login_wrapper.py.tmpl` (failure branch message)
- Test: `tests/services/test_verify_error_message.py`

**Interfaces:**
- Consumes: `capture_login_session` (returns `{ok, screenshot_b64, error}`).
- Produces: on a login that reaches a page but fails the success-check, `error` reads: `Logged in and reached '<url>', but success check '<check>' was not found on the page.`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/services/test_verify_error_message.py
import http.server, socketserver, threading
import pytest
from backend.services.playwright_login import capture_login_session

LOGIN = """<!doctype html><form><input placeholder="Username" id="u"><input type="password" id="p">
<button id="go">Sign in</button><script>document.querySelector('#go').addEventListener('click',function(e){
e.preventDefault(); if(document.querySelector('#p').value==='secret'){location.href='/home';}});</script></form>"""
HOME = "<!doctype html><body><h1>Welcome</h1></body>"


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = LOGIN if self.path.startswith("/login") else HOME
        self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self,*a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


async def test_error_names_reached_url_and_missing_check(server, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "secret",
            "selectors": {}, "success_check": "NONEXISTENT_TEXT"}
    res = await capture_login_session(auth, base_url=server, project_id="p")
    assert res["ok"] is False
    assert "/home" in res["error"] and "NONEXISTENT_TEXT" in res["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_verify_error_message.py -v`
Expected: FAIL (error is the generic "did not reach the expected state").

- [ ] **Step 3: Improve the failure message in the login wrapper**

In `backend/services/playwright_login_wrapper.py.tmpl`, in the `if not ok:` branch, replace the generic message with one that names the reached URL and the check:

```python
                if not ok:
                    if SUCCESS_CHECK:
                        result["error"] = (
                            "Logged in and reached '" + page.url + "', but success check '"
                            + SUCCESS_CHECK + "' was not found on the page."
                        )
                    else:
                        result["error"] = "Login did not leave the login page (check credentials/selectors)."
```

(Keep the `storage_state` save in the `else` success branch unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_verify_error_message.py -v`
Expected: PASS. Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/playwright_login_wrapper.py.tmpl tests/services/test_verify_error_message.py
git commit -m "feat: clearer login-verify error (reached URL + failed check)"
```

---

### Task 6: Frontend — App home path field + login-test toggle

**Files:**
- Modify: `frontend/index.html` (Login setup: add App home path field; Auto-execute modal: add "Login test (run logged out)" checkbox)
- Modify: `frontend/app.js` (`_authBody` sends `home_path`; `renderAuthConfig` fills it; auto-exec passes `login_mode`/`logged_out`)

**Interfaces:**
- Consumes: Task 4 endpoints (`home_path`, `login_mode`, `logged_out`).

- [ ] **Step 1: Add the App home path field in `index.html`**

In the Login setup card, add a fourth input to the main grid (next to Success check):
```html
            <input id="authHomePath" type="text" placeholder="App home path after login (e.g. /inventory.html, optional)" class="text-sm rounded px-3 py-2" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
```

- [ ] **Step 2: Add the login-test toggle in the Auto-execute modal**

Next to the existing "Watch it run" checkbox block in the auto-exec modal, add:
```html
        <div class="flex items-center gap-2">
          <input type="checkbox" id="autoExecLoginTest" />
          <label for="autoExecLoginTest" class="text-sm" style="color:var(--text-secondary);">Login test (run logged out, use saved credentials)</label>
        </div>
```

- [ ] **Step 3: Wire in `app.js`**

In `renderAuthConfig`, add:
```javascript
  el("authHomePath").value = cfg.home_path || "";
```
In `_authBody`, add `home_path` to the returned object:
```javascript
    home_path: el("authHomePath").value.trim(),
```
In `regenerateAutoExecCode`, send `login_mode` when the toggle is checked:
```javascript
      body: JSON.stringify(el("autoExecLoginTest")?.checked ? { regenerate: true, login_mode: true } : { regenerate: true }),
```
In `runAutoExec`, include `logged_out` in the body:
```javascript
      body: JSON.stringify({ code, headless, logged_out: !!el("autoExecLoginTest")?.checked }),
```
In `openAutoExecModal`, reset the toggle when opening:
```javascript
  el("autoExecLoginTest").checked = false;
```

- [ ] **Step 4: Smoke check (drive the app, no real external login)**

Start the server on port 8090, load a project's Auto-execute modal with Playwright headless, assert `#authHomePath` (in Overview) and `#autoExecLoginTest` (in the modal) exist and there are no console errors. Do not mutate the user's real projects; restore `data/testgen.db` if you create anything. Report the empty console-error list.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: App home path field and login-test toggle"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/auto-execute.md` (document landing path, login-test toggle, credential injection)
- Modify: `docs/limitations.md` (note login-flow tests + landing path now handled; DOM-aware selectors still the open #2)

- [ ] **Step 1: Update `docs/auto-execute.md`**

Under "Authenticated runs (login)", add: authenticated tests start on the **App home path** (set in Login setup, or auto-derived from a path-style Success check) instead of `/`; **login-flow tests** (auto-detected, or via the "Login test" toggle) run logged-out and receive the saved username/password injected at run time (never stored in code).

- [ ] **Step 2: Update `docs/limitations.md`**

In the Auto-execute section, update the "Selectors are guessed blind" bullet to note the **path** is now taken from the configured landing page (selectors are still guessed — DOM-aware generation remains fix #2). Under Proposed fixes, leave #2 as the open item.

- [ ] **Step 3: Commit**

```bash
git add docs/auto-execute.md docs/limitations.md
git commit -m "docs: landing path, login-test mode, credential injection"
```

---

## Self-review notes
- **Spec coverage:** landing path (T1 helper, T3 prompt, T4 wiring, T6 field) · login detection (T1, T4, T6 toggle) · credential injection (T2 runner, T3 prompt, T4 forwarding) · verify error (T5) · docs (T7). All spec sections map to a task.
- **Signatures consistent:** `resolve_landing_path(auth_config)->str`, `is_login_test(title, steps)->bool`, `run_playwright_code(code, base_url, headless, storage_state_path=None, username="", password="")`, `build_playwright_user_message(tc, base_url, *, is_login, landing_path, has_credentials)`, `generate_playwright_code(..., *, is_login, landing_path, has_credentials)`.
- **Backward compat:** 2-arg `test` still dispatched (T2); `home_path`/`login_mode`/`logged_out` all default to no-change behavior.
- **Isolation:** router tests use `tests/routers/conftest.py`; service integration tests monkeypatch `_data_dir` to tmp.
```
