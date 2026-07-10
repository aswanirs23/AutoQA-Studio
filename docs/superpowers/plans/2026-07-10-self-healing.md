# DOM-grounded Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** On a failed auto-execute run, let the user confirm the observed page is correct and heal BOTH the `expected_result` and the Playwright code, grounded in the actual failed page's DOM snapshot, then re-run to confirm — with a preview/approve step.

**Architecture:** The runner captures a compact accessibility snapshot of the page at failure time and returns it with the run result. A new `/heal` endpoint feeds that snapshot + current code to the LLM (via the existing JSON-forcing `_complete_json`) to produce a corrected `expected_result` and code. The existing "Update Expected Result" modal gains a proposed-code panel; on save it PATCHes the expected result, saves the code, and re-runs.

**Tech Stack:** Python 3.11, FastAPI, Playwright (async, Chromium), pytest (`asyncio_mode=auto`), vanilla-JS SPA.

## Global Constraints

- No schema change — `page_snapshot` is transient (run response only); reuse `expected_result` (PATCH) and `playwright_code` (`save-playwright`) storage.
- Healed code must preserve the test function's existing signature (2-arg or 4-arg). Credentials never enter generated/healed code (runtime injection only).
- The user-code denylist + env-scrub + 60s timeout stay intact; healed code runs through the same sandbox as any auto-execute code.
- Snapshot is size-capped (6000 chars) to bound payload/prompt size.
- Router tests use the isolated `tests/routers/conftest.py`; service integration tests monkeypatch `backend.services.playwright_login._data_dir` to a tmp dir. No real dev DB, no real network in tests (stub the LLM).
- No `Co-Authored-By` trailers. Use the project venv (`source .venv/bin/activate`). Run `python -m pytest -q` green before each backend commit.

---

### Task 1: Runner captures `page_snapshot` at failure time

**Files:**
- Modify: `backend/services/playwright_runner_wrapper.py.tmpl` (add `_page_snapshot`; set `result["page_snapshot"]`)
- Modify: `backend/routers/playwright_exec.py` (`RunResponse.page_snapshot`; map it in `run_playwright`)
- Test: `tests/services/test_page_snapshot_capture.py`

**Interfaces:**
- Produces: `run_playwright_code(...)` result dict now includes `"page_snapshot": str` (`""` when unavailable); `RunResponse` gains `page_snapshot: str = ""`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/services/test_page_snapshot_capture.py
import http.server, socketserver, threading
import pytest
from backend.services.playwright_runner import run_playwright_code

PAGE = """<!doctype html><body>
<h1>Dashboard</h1><button id="go">Open Menu</button>
<nav aria-label="Main">home</nav></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE.encode())
    def log_message(self, *a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


async def test_failed_run_captures_page_snapshot(server):
    # This assertion is false, so the run fails and must capture a snapshot.
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('NOT PRESENT').count() > 0, 'nope'\n")
    res = await run_playwright_code(code, server, headless=True)
    assert res["status"] == "failed"
    snap = res.get("page_snapshot", "")
    assert snap and ("Open Menu" in snap or "Dashboard" in snap), snap


async def test_passed_run_has_empty_snapshot(server):
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('Dashboard').count() > 0\n")
    res = await run_playwright_code(code, server, headless=True)
    assert res["status"] == "passed"
    assert res.get("page_snapshot", "") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/services/test_page_snapshot_capture.py -v`
Expected: FAIL (`page_snapshot` key absent / empty on the failed run).

- [ ] **Step 3: Add the snapshot helper + capture in the wrapper**

In `backend/services/playwright_runner_wrapper.py.tmpl`, add this helper next to `_page_context` (note doubled braces for literal `{{}}`):

```python
async def _page_snapshot(page) -> str:
    """Compact accessibility snapshot of the failed page for DOM-grounded healing.
    Interactive/labeled nodes only (role + name + value), capped to bound size."""
    try:
        tree = await page.accessibility.snapshot()
    except Exception:
        return ""
    if not tree:
        return ""
    lines: list[str] = []

    def walk(node, depth=0):
        if len(lines) > 400:
            return
        role = node.get("role", "")
        name = (node.get("name", "") or "").strip()
        value = (node.get("value", "") or "").strip()
        if role and (name or value or role in ("button", "textbox", "link", "checkbox")):
            frag = f"{{'  ' * min(depth, 6)}}{{role}}: {{name}}"
            if value:
                frag += f" = {{value}}"
            lines.append(frag)
        for child in node.get("children", []) or []:
            walk(child, depth + 1)

    walk(tree)
    return "\n".join(lines)[:6000]
```

Set the default in the initial `result` dict (add `"page_snapshot": ""`):

```python
    result = {{"status": "error", "screenshot_b64": None, "error_message": None,
              "console_log": "", "duration_ms": 0, "page_snapshot": ""}}
```

In BOTH the `except AssertionError` and the generic `except Exception` (failure/error) branches, after capturing the screenshot, add:

```python
                        result["page_snapshot"] = await _page_snapshot(page)
```

(Do NOT set it in the `passed` branch — it stays `""`.)

- [ ] **Step 4: Thread it into the API response**

In `backend/routers/playwright_exec.py`, add to `RunResponse`:

```python
    page_snapshot: str = ""
```
and in `run_playwright`, where `RunResponse(...)` is constructed from `result`, add:

```python
        page_snapshot=result.get("page_snapshot", "") or "",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_page_snapshot_capture.py -v`
Expected: PASS (both). Then `python -m pytest -q` (full suite green; existing runner tests unaffected — new key is additive).

- [ ] **Step 6: Commit**

```bash
git add backend/services/playwright_runner_wrapper.py.tmpl backend/routers/playwright_exec.py tests/services/test_page_snapshot_capture.py
git commit -m "feat: capture DOM snapshot of the failed page for healing"
```

---

### Task 2: Heal prompt + `heal_test_case` service

**Files:**
- Modify: `backend/prompts/templates.py` (add `HEAL_SYSTEM_PROMPT`, `build_heal_prompt`)
- Modify: `backend/services/llm_service.py` (add `heal_test_case`)
- Test: `tests/services/test_heal_prompt.py`

**Interfaces:**
- Consumes: `_complete_json(system, user, settings, provider_override, model_override=None) -> str` (existing, returns a JSON string).
- Produces:
  - `build_heal_prompt(tc: dict, current_code: str, page_snapshot: str, error_message: str) -> str`
  - `heal_test_case(tc_dict: dict, current_code: str, page_snapshot: str, error_message: str, settings, provider_override=None, model_override=None) -> tuple[str, str]` returning `(suggested_expected, suggested_code)`. Raises `ValueError` on empty/malformed LLM output.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_heal_prompt.py
from backend.prompts.templates import build_heal_prompt

TC = {"title": "Verify nav menu opens", "steps": ["Click the menu icon"], "expected_result": "menu shows"}


def test_heal_prompt_includes_snapshot_code_and_directive():
    msg = build_heal_prompt(TC, "async def test(page, base_url):\n    assert False\n",
                            "button: Open Menu\nnav: Main", "AssertionError: nope")
    assert "button: Open Menu" in msg          # the observed snapshot
    assert "async def test(page, base_url):" in msg  # the current code
    assert "observed" in msg.lower()           # 'the observed page is correct' directive
    assert "suggested_expected" in msg and "suggested_code" in msg  # JSON contract
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_heal_prompt.py -v`
Expected: FAIL (ImportError — `build_heal_prompt` not defined).

- [ ] **Step 3: Add the prompt + builder**

Append to `backend/prompts/templates.py`:

```python
HEAL_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer performing SELF-HEALING of a failed "
    "Playwright test. The test failed, but the user has confirmed the page's ACTUAL "
    "observed behavior is CORRECT. Treat the provided accessibility snapshot of the "
    "observed page as the source of truth.\n"
    "Do two things:\n"
    "1. Rewrite the test case's expected_result to accurately describe the observed "
    "behavior.\n"
    "2. Fix the Playwright test code so its selectors and assertions match the observed "
    "snapshot and PASS against that page. Preserve the code's navigation/step intent and "
    "its EXACT function signature (keep `async def test(page, base_url)` or "
    "`async def test(page, base_url, username, password)` as given). Do not hard-code "
    "credentials.\n"
    "Respond with ONLY a JSON object of the form "
    '{"suggested_expected": "<text>", "suggested_code": "<python source>"}. '
    "No markdown, no commentary."
)


def build_heal_prompt(tc: dict, current_code: str, page_snapshot: str, error_message: str) -> str:
    steps_section = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(tc.get('steps') or []))
    return (
        "Heal this failed test using the observed page as the correct behavior.\n\n"
        f"Title: {tc.get('title', '')}\n"
        f"Steps:\n{steps_section}\n"
        f"Current expected_result: {tc.get('expected_result', '')}\n\n"
        f"Failure message:\n{error_message}\n\n"
        f"Observed page (accessibility snapshot — this is correct):\n{page_snapshot}\n\n"
        f"Current Playwright code:\n{current_code}\n\n"
        "Return JSON with keys suggested_expected and suggested_code."
    )
```

- [ ] **Step 4: Add `heal_test_case` to `llm_service.py`**

Append:

```python
async def heal_test_case(
    tc_dict: dict,
    current_code: str,
    page_snapshot: str,
    error_message: str,
    settings: Settings,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """Rewrite expected_result + Playwright code from the observed page snapshot.
    Returns (suggested_expected, suggested_code). Raises ValueError on malformed output."""
    import json as _json
    from backend.prompts.templates import HEAL_SYSTEM_PROMPT, build_heal_prompt

    user = build_heal_prompt(tc_dict, current_code, page_snapshot, error_message)
    raw = await _complete_json(HEAL_SYSTEM_PROMPT, user, settings, provider_override, model_override)
    try:
        data = _json.loads(raw)
        expected = str(data["suggested_expected"]).strip()
        code = str(data["suggested_code"]).strip()
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"Heal returned malformed output: {e}") from e
    if not expected or not code:
        raise ValueError("Heal returned an empty expected_result or code")
    return expected, code
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_heal_prompt.py -v`
Expected: PASS. Then `python -m pytest -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/prompts/templates.py backend/services/llm_service.py tests/services/test_heal_prompt.py
git commit -m "feat: heal prompt and heal_test_case service"
```

---

### Task 3: `/heal` endpoint (with text-only fallback)

**Files:**
- Modify: `backend/routers/playwright_exec.py` (`HealBody`, `HealResponse`, `POST .../heal`)
- Test: `tests/routers/test_heal_endpoint.py`

**Interfaces:**
- Consumes: `heal_test_case(...)` (Task 2); existing `suggest_expected_result(...)`, `generate_playwright_code(...)`, `project_repo.get_project`, `testcase_repo.get_test_case`.
- Produces: `POST /api/projects/{pid}/test-cases/{tcid}/heal`, body `HealBody { current_code: str, page_snapshot: str = "", error_message: str = "" }`, returns `HealResponse { suggested_expected: str, suggested_code: str }`.

- [ ] **Step 1: Write the failing test**

```python
# tests/routers/test_heal_endpoint.py
from unittest.mock import AsyncMock, patch


def test_heal_returns_expected_and_code_from_snapshot(client, seed_test_case):
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://x/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify nav menu opens")
    with patch("backend.routers.playwright_exec.heal_test_case", new_callable=AsyncMock) as m:
        m.return_value = ("Menu is shown", "async def test(page, base_url):\n    pass\n")
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": "async def test(page, base_url):\n    assert False\n",
                              "page_snapshot": "button: Open Menu", "error_message": "AssertionError"})
    assert r.status_code == 200
    body = r.json()
    assert body["suggested_expected"] == "Menu is shown"
    assert "async def test" in body["suggested_code"]


def test_heal_falls_back_to_text_only_when_no_snapshot(client, seed_test_case):
    pid = client.post("/api/projects", json={"name": "P2", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://x/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Nav test")
    with patch("backend.routers.playwright_exec.suggest_expected_result", new_callable=AsyncMock) as ms, \
         patch("backend.routers.playwright_exec.generate_playwright_code", new_callable=AsyncMock) as mg:
        ms.return_value = "Observed text expected"
        mg.return_value = "async def test(page, base_url):\n    pass\n"
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": "x", "page_snapshot": "", "error_message": "boom"})
    assert r.status_code == 200
    assert r.json()["suggested_expected"] == "Observed text expected"
    ms.assert_awaited_once(); mg.assert_awaited_once()  # fallback path used
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/routers/test_heal_endpoint.py -v`
Expected: FAIL (404 — route not defined).

- [ ] **Step 3: Add the endpoint**

In `backend/routers/playwright_exec.py`, add the import:

```python
from backend.services.llm_service import generate_playwright_code, heal_test_case
```
(extend the existing `generate_playwright_code` import line; `suggest_expected_result` is already imported lazily inside `suggest_expected` — add a module-level import for it too: `from backend.services.llm_service import suggest_expected_result`.)

Add the models near the other bodies:

```python
class HealBody(BaseModel):
    current_code: str
    page_snapshot: str = ""
    error_message: str = ""


class HealResponse(BaseModel):
    suggested_expected: str
    suggested_code: str
```

Add the route:

```python
@router.post("/{project_id}/test-cases/{test_case_id}/heal", response_model=HealResponse)
async def heal(
    project_id: str,
    test_case_id: str,
    body: HealBody,
    user_id: str = Depends(get_current_user_id),
) -> HealResponse:
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        tc = await testcase_repo.get_test_case(db, project_id, test_case_id)
        if not tc:
            raise HTTPException(status_code=404, detail="Test case not found")
    settings = get_effective_settings()
    tc_dict = {"title": tc.title, "preconditions": tc.preconditions,
               "steps": tc.steps, "expected_result": tc.expected_result}
    try:
        if body.page_snapshot.strip():
            expected, code = await heal_test_case(
                tc_dict, body.current_code, body.page_snapshot, body.error_message, settings)
        else:
            # Fallback: no DOM snapshot (e.g. error before page load) → text-only heal.
            expected = await suggest_expected_result(
                current_expected_result=tc.expected_result,
                actual_page_text=body.error_message,
                error_message=body.error_message,
                settings=settings)
            base_url = (proj.base_url or "").strip().rstrip("/")
            code = await generate_playwright_code(tc_dict, base_url, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("LLM error", e) from e
    return HealResponse(suggested_expected=expected, suggested_code=code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/routers/test_heal_endpoint.py -v`
Expected: PASS (both). Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/playwright_exec.py tests/routers/test_heal_endpoint.py
git commit -m "feat: /heal endpoint with DOM-grounded and text-only fallback paths"
```

---

### Task 4: Frontend — proposed-code panel + heal wiring + save both + re-run

**Files:**
- Modify: `frontend/index.html` (adaptExpectedModal: add `#adaptSuggestedCode` panel; remove the `#adaptRegenerateCode` checkbox block)
- Modify: `frontend/app.js` (`openAdaptExpectedModal`/`_fetchAdaptSuggestion` call `/heal`; `saveAndRerunAdaptedExpected` saves code + expected)

**Interfaces:**
- Consumes: Task 1 `page_snapshot` in the run response; Task 3 `/heal`; existing `save-playwright`, PATCH, `run-playwright`.

- [ ] **Step 1: Add the proposed-code panel in `index.html`**

In `#adaptExpectedModal`, immediately after the block containing `#adaptSuggestedText`, add:

```html
        <div>
          <label for="adaptSuggestedCode" class="text-xs" style="color:var(--text-tertiary);">Proposed code (editable)</label>
          <textarea id="adaptSuggestedCode" rows="10" class="w-full text-xs rounded px-3 py-2 font-mono" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" spellcheck="false"></textarea>
        </div>
```

Remove the checkbox block that contains `id="adaptRegenerateCode"` (the "Regenerate Playwright code from new expected" label) — code is always healed now.

- [ ] **Step 2: Wire heal into `openAdaptExpectedModal` / `_fetchAdaptSuggestion`**

In `frontend/app.js`, in `openAdaptExpectedModal`, replace the line `el("adaptRegenerateCode").checked = false;` with:

```javascript
  el("adaptSuggestedCode").value = "Loading suggestion...";
```

Replace the body of `_fetchAdaptSuggestion` with a call to `/heal` (it now returns both fields):

```javascript
async function _fetchAdaptSuggestion() {
  if (!_adaptContext) return;
  const { tcId, errorMessage, runResult } = _adaptContext;
  const tc = lastLoadedCases.find(c => c.id === tcId);
  const currentCode = tc?.playwright_code || (el("autoExecCode")?.value || "");
  el("adaptSuggestedText").value = "Loading suggestion...";
  el("adaptSuggestedCode").value = "Loading suggestion...";
  el("btnAdaptExpectedSave").disabled = true;
  try {
    const resp = await fetchJSON(
      `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/heal`,
      { method: "POST", body: JSON.stringify({
          current_code: currentCode,
          page_snapshot: runResult?.page_snapshot || "",
          error_message: errorMessage,
        }) },
    );
    el("adaptSuggestedText").value = resp.suggested_expected || "";
    el("adaptSuggestedCode").value = resp.suggested_code || "";
    el("btnAdaptExpectedSave").disabled = false;
  } catch (e) {
    el("adaptSuggestedText").value = "";
    el("adaptSuggestedCode").value = "";
    showToast(String(e.message || e), true);
    el("btnAdaptExpectedSave").disabled = false;
  }
}
```

- [ ] **Step 3: Save code + expected in `saveAndRerunAdaptedExpected`**

In `saveAndRerunAdaptedExpected`, after the PATCH of `expected_result` succeeds (and before the re-run), persist the edited code. Replace the `const regenCode = ...` line and the code-regen handling with an unconditional save of the proposed code:

```javascript
  const newCode = (el("adaptSuggestedCode")?.value || "").trim();
```
and, immediately after the successful PATCH block (after `if (tc) tc.expected_result = newExpected;`), add:

```javascript
  if (newCode) {
    try {
      await fetchJSON(
        `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/save-playwright`,
        { method: "POST", body: JSON.stringify({ code: newCode }) });
      const tc2 = lastLoadedCases.find(c => c.id === tcId);
      if (tc2) tc2.playwright_code = newCode;
    } catch (e) { showToast(String(e.message || e), true); }
  }
```

Then, in the re-run step that follows, run `newCode` (not the old code): ensure the `run-playwright` call uses `{ code: newCode, headless: true }`.

- [ ] **Step 4: Smoke check (drive the app; stub-free UI check)**

Start the server on 8090 (`--reload`), open a project, open a test case's Auto-execute, and confirm the "Update Expected Result" modal now shows a **Proposed code** textarea and no "Regenerate code" checkbox, with no console errors. (A real heal needs a failing run + LLM; the full loop is covered by Task 5.) Do not mutate real projects; restore `data/testgen.db` if you create anything. Report the empty console-error list.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: heal expected result and code in the adapt modal"
```

---

### Task 5: End-to-end self-heal loop integration test

**Files:**
- Test: `tests/routers/test_self_heal_loop.py`

**Interfaces:**
- Consumes: `/run-playwright`, `/heal` (mocked LLM), PATCH, `/save-playwright`.

- [ ] **Step 1: Write the failing test**

```python
# tests/routers/test_self_heal_loop.py
import http.server, socketserver, threading
from unittest.mock import AsyncMock, patch
import pytest

PAGE = "<!doctype html><body><h1>Dashboard</h1><div id='menu'>Menu open</div></body>"


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE.encode())
    def log_message(self, *a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


def test_full_self_heal_loop(client, seed_test_case, server):
    pid = client.post("/api/projects", json={"name": "Heal", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": server})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify dashboard menu")

    # 1. Run WRONG code -> fails, captures a snapshot.
    bad = ("async def test(page, base_url):\n"
           "    await page.goto(base_url + '/')\n"
           "    assert await page.get_by_text('NONEXISTENT').count() > 0, 'wrong'\n")
    r1 = client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
                     json={"code": bad, "headless": True}).json()
    assert r1["status"] == "failed"
    assert r1["page_snapshot"]  # snapshot captured

    # 2. Heal (LLM stubbed to return corrected code + expected).
    good = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('Menu open').count() > 0, 'menu shows'\n")
    with patch("backend.routers.playwright_exec.heal_test_case", new_callable=AsyncMock) as m:
        m.return_value = ("The dashboard menu is shown", good)
        h = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": bad, "page_snapshot": r1["page_snapshot"],
                              "error_message": r1["error_message"]}).json()
    assert h["suggested_code"] == good

    # 3. Save healed expected + code, then re-run -> now PASSES.
    client.patch(f"/api/projects/{pid}/test-cases/{tcid}",
                 json={"expected_result": h["suggested_expected"]})
    client.post(f"/api/projects/{pid}/test-cases/{tcid}/save-playwright",
                json={"code": h["suggested_code"]})
    r2 = client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
                     json={"code": h["suggested_code"], "headless": True}).json()
    assert r2["status"] == "passed", r2
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/routers/test_self_heal_loop.py -v`
Expected: PASS (it exercises Tasks 1+3 against a real local page with a stubbed LLM). If it fails, the defect is in Task 1 or 3 — fix there.

- [ ] **Step 3: Full suite + commit**

Run: `python -m pytest -q` (all pass).
```bash
git add tests/routers/test_self_heal_loop.py
git commit -m "test: end-to-end self-heal loop (fail -> heal -> pass)"
```

---

### Task 6: Docs

**Files:**
- Modify: `docs/auto-execute.md`, `docs/limitations.md`

- [ ] **Step 1: Update `docs/auto-execute.md`**

In the "When a test fails because the wording diverged" section, update it to describe self-healing: on a FAILED run, "✓ Mark as expected behavior" now captures the failed page's DOM and heals **both** the expected result AND the Playwright code (fixing selectors/assertions to match the observed page), which you preview/edit before it saves both and re-runs. Note credentials never enter healed code and the code's signature is preserved.

- [ ] **Step 2: Update `docs/limitations.md`**

In the Auto-execute section, update the "selectors guessed blind" bullet: initial generation still guesses selectors, but a failed run can now be **self-healed against the observed DOM**, so selectors/assertions get corrected from the real page. Under Proposed fixes, note fix #2 (DOM-aware generation) is now partially delivered via the heal path (healing uses the real DOM; first-pass generation still doesn't).

- [ ] **Step 3: Commit**

```bash
git add docs/auto-execute.md docs/limitations.md
git commit -m "docs: DOM-grounded self-healing of failed tests"
```

---

## Self-review notes
- **Spec coverage:** capture at failure (T1) · heal endpoint DOM-grounded + text-only fallback (T2,T3) · modal proposed-code panel + save both + re-run (T4) · graceful fallback (T3) · full loop test (T5) · docs (T6). All spec sections map to a task.
- **Signatures consistent:** `page_snapshot` str throughout (wrapper→result→RunResponse→heal body); `heal_test_case(...) -> (expected, code)`; `build_heal_prompt(tc, current_code, page_snapshot, error_message)`; `/heal` body `{current_code, page_snapshot, error_message}` → `{suggested_expected, suggested_code}`.
- **No credentials in healed code:** heal prompt preserves signature + forbids hard-coded creds; runtime injection (arity dispatch) unchanged.
- **Isolation:** router tests use `tests/routers/conftest.py` + `seed_test_case`; LLM stubbed via `patch("backend.routers.playwright_exec.heal_test_case", ...)`. The `/heal` request payload is exactly `{ current_code, page_snapshot, error_message }`.
```
