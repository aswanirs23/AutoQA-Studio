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

import asyncio
import base64
import logging
import re
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


# -------- Error classification ----------------------------------------------

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


# -------- Driver -----------------------------------------------------------


class BrowserMcpDriver:
    """BrowserDriver implementation that proxies to browsermcp.io's MCP server.

    Lifecycle: ``start()`` spawns ``@browsermcp/mcp`` over stdio, opens an
    MCP ``ClientSession``, runs the initialize handshake, and verifies the
    expected tools are advertised. ``close()`` tears down the exit stack,
    which terminates the subprocess.

    Caches accessibility names from the most recent snapshot so ``click`` /
    ``type`` can pass the human-readable ``element`` arg expected by
    browsermcp's tools. (Subsequent tasks add navigate/snapshot/click/type.)
    """

    def __init__(
        self,
        *,
        mcp_command: str = "npx",
        mcp_args: list[str] | None = None,
        startup_timeout_seconds: float = 30.0,
        tool_timeout_seconds: float = 30.0,
    ) -> None:
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
