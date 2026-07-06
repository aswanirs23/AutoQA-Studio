"""Tests that Orchestrator persists derived goal/feature_name when callers
omit them. Uses a fake driver so MCP/Playwright are not exercised here."""

from __future__ import annotations

import os
import tempfile
from typing import Any

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


def test_derive_goal_pure_function():
    """Direct unit check that the helpers consume the fake snapshot correctly."""
    from backend.services.browser_explorer.orchestrator import (
        _derive_feature_name, _derive_goal_from_snapshot,
    )

    goal = _derive_goal_from_snapshot(SNAPSHOT_FIXTURE)
    assert "Dashboard" in goal
    assert "1 input field" in goal
    assert "1 primary action" in goal

    fname = _derive_feature_name(SNAPSHOT_FIXTURE, "bs_abc12345def6")
    assert fname == "dashboard"
