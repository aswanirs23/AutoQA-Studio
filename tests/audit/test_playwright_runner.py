"""Unit + integration tests for the Playwright auto-execute feature.

Three offline tests run always (no LLM, no network, no Chromium):
- test_generate_playwright_requires_base_url
- test_run_playwright_rejects_denylisted_code
- test_run_playwright_rejects_invalid_url

One Chromium-requiring test runs unless AUDIT_RUN_NETWORK=0:
- test_run_playwright_against_example_com   (requires `playwright install chromium` and internet)

One timeout test runs always (no network, no LLM):
- test_run_playwright_kills_long_running_subprocess

One LLM-gated test runs when AUDIT_RUN_LLM=1:
- test_generate_playwright_happy_path

Run with:
    pytest tests/audit/test_playwright_runner.py -v
    AUDIT_RUN_LLM=1 pytest tests/audit/test_playwright_runner.py -v
"""

from __future__ import annotations

import os
import time

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

RUN_LLM = os.environ.get("AUDIT_RUN_LLM", "0") == "1"
RUN_NETWORK = os.environ.get("AUDIT_RUN_NETWORK", "1") == "1"
BOGUS_UUID = "00000000-0000-0000-0000-000000000000"


# ---------------------------------------------------------------- generate-playwright (validation)

async def test_generate_playwright_requires_base_url(client: AsyncClient) -> None:
    """Without a base_url set on the project, generate-playwright returns 400."""
    r = await client.post("/api/projects", json={"name": "PW gen no-base", "description": "", "context": {}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # No base_url set on the project. We don't have a real TC either, but
    # the base_url check should fire first.
    r = await client.post(f"/api/projects/{pid}/test-cases/TC_000/generate-playwright")
    assert r.status_code == 400, r.text
    body = r.json()
    assert "base_url" in body.get("detail", "").lower() or "base url" in body.get("detail", "").lower()


# ---------------------------------------------------------------- run-playwright (denylist)

async def test_run_playwright_rejects_denylisted_code(client: AsyncClient) -> None:
    """Code containing a denylisted token returns status=error from the runner."""
    from backend.services.playwright_runner import run_playwright_code

    result = await run_playwright_code(
        code='import os\nasync def test(page, base_url):\n    os.system("ls")\n',
        base_url="https://example.com",
        headless=True,
    )
    assert result["status"] == "error"
    assert "safety" in (result.get("error_message") or "").lower()


# ---------------------------------------------------------------- run-playwright (invalid URL)

async def test_run_playwright_rejects_invalid_url(client: AsyncClient) -> None:
    """A non-http(s) base_url is rejected before spawning a subprocess."""
    from backend.services.playwright_runner import run_playwright_code

    result = await run_playwright_code(
        code="async def test(page, base_url):\n    pass\n",
        base_url="ftp://nope",
        headless=True,
    )
    assert result["status"] == "error"
    assert "http" in (result.get("error_message") or "").lower()


# ---------------------------------------------------------------- run-playwright (timeout)

async def test_run_playwright_kills_long_running_subprocess(client: AsyncClient) -> None:
    """A test that hangs forever is killed at the wall-clock timeout.

    The default 60s is monkeypatched down to 3s so the suite stays fast.
    """
    import backend.services.playwright_runner as runner

    original_timeout = runner.TIMEOUT_SECONDS
    runner.TIMEOUT_SECONDS = 3.0
    try:
        started = time.time()
        result = await runner.run_playwright_code(
            code=(
                "import asyncio\n"
                "async def test(page, base_url):\n"
                "    await asyncio.sleep(120)\n"
            ),
            base_url="https://example.com",
            headless=True,
        )
        elapsed = time.time() - started
        assert result["status"] == "error"
        assert "timeout" in (result.get("error_message") or "").lower()
        assert elapsed < 15, f"Timeout took too long: {elapsed:.1f}s"
    finally:
        runner.TIMEOUT_SECONDS = original_timeout


# ---------------------------------------------------------------- run-playwright (network happy path)

@pytest.mark.skipif(not RUN_NETWORK, reason="Set AUDIT_RUN_NETWORK=0 to skip network tests")
async def test_run_playwright_against_example_com(client: AsyncClient) -> None:
    """End-to-end run against https://example.com — no LLM needed.

    Prerequisites:
    - `playwright install chromium` has been run on the host
    - Internet connectivity from the test host
    """
    from backend.services.playwright_runner import run_playwright_code

    code = (
        "async def test(page, base_url):\n"
        "    await page.goto(base_url)\n"
        "    title = await page.title()\n"
        "    assert 'Example' in title\n"
    )
    result = await run_playwright_code(code, base_url="https://example.com", headless=True)
    if result["status"] != "passed":
        # Surface the runner's error_message in the assertion to make CI debugging easy
        raise AssertionError(
            f"Expected status=passed; got {result['status']}. "
            f"error_message={result.get('error_message')!r}"
        )
    assert result.get("screenshot_b64"), "Expected a screenshot for a passing test"
    assert result["duration_ms"] > 0


# ---------------------------------------------------------------- generate-playwright (LLM-gated)

@pytest.mark.skipif(not RUN_LLM, reason="Set AUDIT_RUN_LLM=1 to exercise LLM-bound endpoints")
async def test_generate_playwright_happy_path(client: AsyncClient) -> None:
    """LLM returns a Playwright function for a real test case.

    Creates a project, sets base_url, generates a TC via the text parser, then
    asks for Playwright code. Asserts the response contains `async def test`
    and is free of denylisted tokens.
    """
    r = await client.post("/api/projects", json={"name": "PW gen happy", "description": "", "context": {}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = await client.put(f"/api/projects/{pid}", json={"base_url": "https://example.com"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/projects/{pid}/features", json={"name": "Home", "description": "", "sort_order": 0})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    r = await client.post("/api/generate", json={
        "input_type": "text",
        "project_id": pid,
        "feature_id": fid,
        "data": {"feature_name": "Home", "content": "Page loads. Title contains 'Example'."},
    })
    assert r.status_code == 200, r.text
    cases = r.json()["test_cases"]
    assert cases, "Expected at least one test case from text generation"
    tcid = cases[0]["id"]

    r = await client.post(f"/api/projects/{pid}/test-cases/{tcid}/generate-playwright")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert "async def test" in code
    from backend.services.playwright_runner import _check_denylist
    assert _check_denylist(code) is None, (
        f"LLM output contains denylisted token: {_check_denylist(code)}"
    )
