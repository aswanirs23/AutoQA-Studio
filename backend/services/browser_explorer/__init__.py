"""AI-driven browser exploration package.

Public entry point: ``run_exploration``. Spawns a BrowserDriver, runs the
LLM tool loop, and returns the populated ``ExplorationLedger`` for
downstream test-case authoring.

Windows note: uvicorn forces a ``SelectorEventLoop`` on the host process,
which can't spawn subprocesses, which makes Playwright's Node driver fail
with a bare ``NotImplementedError``. We sidestep this by running the
orchestrator's coroutine in a dedicated thread with a fresh
``ProactorEventLoop`` whenever the calling loop is a Selector. The thread
inherits the user's ``on_progress`` callback; aiosqlite is loop-agnostic
since it spawns its own SQLite worker thread internally.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sys
import threading
from typing import Awaitable, Callable

from backend.config import Settings, effective_llm_provider, get_effective_settings, resolved_model_id
from backend.services.browser_explorer.budget import Budget
from backend.services.browser_explorer.drivers import BrowserDriver
from backend.services.browser_explorer.drivers.mcp_driver import BrowserMcpDriver
from backend.services.browser_explorer.drivers.playwright_driver import PlaywrightDriver
from backend.services.browser_explorer.ledger import ExplorationLedger
from backend.services.browser_explorer.orchestrator import Orchestrator
from backend.services.llm_tool_loop import ToolLoopResult

logger = logging.getLogger(__name__)


def _current_loop_supports_subprocess() -> bool:
    """Heuristic: SelectorEventLoop on Windows can't spawn subprocesses.

    Anything else (Proactor on Windows, the default loop on Linux/macOS,
    uvloop) is fine.
    """
    if sys.platform != "win32":
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    return "Selector" not in type(loop).__name__


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


async def run_exploration(
    *,
    session_id: str,
    goal: str,
    starting_url: str,
    settings: Settings | None = None,
    driver_name: str | None = None,
    read_only: bool | None = None,
    max_actions: int | None = None,
    max_pages: int | None = None,
    max_seconds: int | None = None,
    headless: bool = True,
    host_allowlist: list[str] | None = None,
    on_progress: Callable[[ExplorationLedger], Awaitable[None]] | None = None,
) -> tuple[ExplorationLedger, ToolLoopResult]:
    """Run an AI-driven browser exploration end-to-end.

    Returns ``(ledger, tool_loop_result)``. The caller is responsible for
    persisting the ledger (typically into ``BrowserSession.metadata``) and
    handing it to the Author phase.

    On Windows, when called from a SelectorEventLoop (uvicorn's default),
    the actual run is delegated to a worker thread with its own
    ProactorEventLoop so Playwright can spawn its Node subprocess.
    """
    kwargs = dict(
        session_id=session_id,
        goal=goal,
        starting_url=starting_url,
        settings=settings,
        driver_name=driver_name,
        read_only=read_only,
        max_actions=max_actions,
        max_pages=max_pages,
        max_seconds=max_seconds,
        headless=headless,
        host_allowlist=host_allowlist,
        on_progress=on_progress,
    )

    if _current_loop_supports_subprocess():
        return await _run_exploration_impl(**kwargs)

    logger.info(
        "Current event loop is %s; running exploration in a worker thread "
        "with a ProactorEventLoop so Playwright can spawn subprocesses.",
        type(asyncio.get_running_loop()).__name__,
    )
    return await _run_in_proactor_thread(kwargs)


async def _run_exploration_impl(
    *,
    session_id: str,
    goal: str,
    starting_url: str,
    settings: Settings | None,
    driver_name: str | None,
    read_only: bool | None,
    max_actions: int | None,
    max_pages: int | None,
    max_seconds: int | None,
    headless: bool,
    host_allowlist: list[str] | None,
    on_progress: Callable[[ExplorationLedger], Awaitable[None]] | None,
) -> tuple[ExplorationLedger, ToolLoopResult]:
    s = settings or get_effective_settings()
    driver_name = driver_name or s.browser_explorer_default_driver
    read_only = s.browser_explorer_read_only if read_only is None else read_only
    max_actions = max_actions or s.browser_explorer_max_actions
    max_pages = max_pages or s.browser_explorer_max_pages
    max_seconds = max_seconds or s.browser_explorer_max_seconds

    if not host_allowlist:
        from urllib.parse import urlparse

        host = urlparse(starting_url).hostname
        host_allowlist = [host] if host else None

    screenshot_dir = os.path.join(s.browser_explorer_screenshot_dir, session_id)

    driver = _build_driver(
        driver_name, headless=headless, host_allowlist=host_allowlist, settings=s,
    )
    ledger = ExplorationLedger(session_id=session_id, goal=goal, starting_url=starting_url)
    budget = Budget(max_actions=max_actions, max_pages=max_pages, max_seconds=max_seconds)
    provider = effective_llm_provider(s, None)
    model_id = resolved_model_id(s, provider, None)

    orch = Orchestrator(
        driver=driver,
        ledger=ledger,
        budget=budget,
        settings=s,
        model_id=model_id,
        screenshot_dir=screenshot_dir,
        read_only=read_only,
        on_progress=on_progress,
    )
    result = await orch.run()
    return ledger, result


async def _run_in_proactor_thread(kwargs: dict) -> tuple[ExplorationLedger, ToolLoopResult]:
    """Run ``_run_exploration_impl`` in a worker thread with a fresh
    ProactorEventLoop. The on_progress callback runs inside this loop too —
    aiosqlite is loop-agnostic (uses its own SQLite worker thread) so DB
    persistence keeps working.
    """
    fut: concurrent.futures.Future = concurrent.futures.Future()

    def thread_main() -> None:
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_run_exploration_impl(**kwargs))
                fut.set_result(result)
            finally:
                try:
                    loop.close()
                except Exception:
                    logger.exception("error closing thread proactor loop")
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)

    threading.Thread(target=thread_main, daemon=True, name=f"browser-explore-{kwargs.get('session_id')}").start()
    return await asyncio.wrap_future(fut)


__all__ = ["run_exploration", "ExplorationLedger"]
