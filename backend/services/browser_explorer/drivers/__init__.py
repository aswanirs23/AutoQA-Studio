"""Browser-driver protocol used by the AI explorer.

The orchestrator works against this Protocol, not a concrete driver. Phase 1
ships ``PlaywrightDriver`` (self-contained, headless or headed Chromium).
Phase 2 adds ``BrowserMcpDriver`` that connects to the user's local Browser
MCP server, reusing their authenticated Chrome session.

A "ref" is a stable string ID assigned to an interactive element during
``snapshot()``. The orchestrator validates that every action's target ref
exists in the latest snapshot before forwarding it to the driver. The
driver does not need to validate refs — it should just look them up in
its own registry and either act or raise ``RefNotFoundError``.
"""

from __future__ import annotations

from typing import Any, Protocol


class RefNotFoundError(Exception):
    """Raised when a tool tries to act on a ref that no longer exists."""


class BrowserNotConnectedError(RuntimeError):
    """Raised when an MCP-backed driver call hits the server but no Chrome
    tab is connected (user hasn't clicked "Connect" on the browsermcp.io
    extension, or the tab was closed mid-run).
    """


class Snapshot(dict):
    """Loose dict shape so JSON serialization is trivial.

    Required keys:
        url           : current page URL
        title         : current page title
        elements      : list of {ref, role, name, tag, testid?, text?, disabled?}
        text_dump     : flat text for state-hash dedup
        summary       : short human-readable structure summary for the ledger
    """


class BrowserDriver(Protocol):
    """Async driver interface; one instance per exploration run."""

    async def start(self) -> None:
        """Launch the underlying browser."""
        ...

    async def navigate(self, url: str) -> None:
        """Load the given URL and wait for it to settle."""
        ...

    async def snapshot(self) -> Snapshot:
        """Take a fresh accessibility snapshot, assigning refs to every
        interactive element. Subsequent ``click`` / ``type`` calls operate
        against refs from this snapshot.
        """
        ...

    async def click(self, ref: str) -> dict[str, Any]:
        """Click the element identified by ref. Returns ``{"ok": True}`` on
        success or raises ``RefNotFoundError``.
        """
        ...

    async def type(self, ref: str, value: str) -> dict[str, Any]:
        """Focus the element identified by ref and type the given value.
        Returns ``{"ok": True}`` on success or raises ``RefNotFoundError``.
        """
        ...

    async def screenshot(self, path: str) -> str:
        """Capture a PNG screenshot to the given file path. Returns the path."""
        ...

    async def current_url(self) -> str:
        ...

    async def page_title(self) -> str:
        ...

    async def close(self) -> None:
        """Shut down the browser cleanly."""
        ...
