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
        assert snap["title"]  # should be non-empty
        assert "example.com" in snap["url"]
    finally:
        await driver.close()
