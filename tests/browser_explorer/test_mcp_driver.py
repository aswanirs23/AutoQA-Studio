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
