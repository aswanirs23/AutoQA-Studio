"""Orchestrator — wires the BrowserDriver, LLM tool loop, Evidence Ledger,
Budget, and per-tool anti-hallucination guards into a single ``run()``.

Responsibilities:
- Define the tool surface exposed to the LLM (navigate / snapshot / click /
  type / screenshot / done).
- Translate tool calls into BrowserDriver actions, validating refs against
  the latest snapshot before forwarding.
- Detect no-ops (URL + accessibility-tree hash unchanged after action) and
  record them with ``outcome="no_op"`` so the Author can never write tests
  for behavior that didn't happen.
- Dedup pages by snapshot hash so the LLM is nudged out of "click same nav
  link three times" loops.
- Block destructive actions when ``read_only=True``.
- Materialize input values via ``value_gen`` from template names so the LLM
  never types raw strings.
- Maintain the running ``ExplorationLedger`` and persist it after every
  tool call so the live status endpoint can show progress.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Awaitable, Callable

from backend.config import Settings
from backend.db import get_db
from backend.prompts.explorer import EXPLORER_SYSTEM_PROMPT, explorer_user_message
from backend.services import browser_session as bs_service
from backend.services.browser_explorer.budget import Budget, BudgetExceeded
from backend.services.browser_explorer.drivers import BrowserDriver, RefNotFoundError
from backend.services.browser_explorer.ledger import ExplorationLedger, hash_snapshot
from backend.services.browser_explorer.value_gen import TEMPLATES, generate as gen_value
from backend.services.llm_tool_loop import Tool, ToolLoopResult, run_tool_loop

logger = logging.getLogger(__name__)


def _derive_goal_from_snapshot(snap: dict) -> str:
    """Build a focused exploration goal from the first snapshot.

    Used when the explore caller didn't provide a goal. Falls back to a
    generic "explore everything" goal when the page has no obvious
    affordances (no buttons, no input-like elements).
    """
    title = (snap.get("title") or "").strip()
    elements = snap.get("elements") or []
    forms = sum(
        1 for el in elements
        if el.get("role") in ("textbox", "combobox", "checkbox", "radio")
    )
    buttons = [
        el.get("name") or "" for el in elements
        if el.get("role") == "button" and el.get("name")
    ]
    n_buttons = len(buttons)

    if n_buttons == 0 and forms == 0:
        return ("Explore this page and document all features, forms, "
                "validations, and errors you encounter.")

    pieces: list[str] = []
    if forms:
        pieces.append(
            f"verify the {forms} input field(s) including validation and error handling"
        )
    if n_buttons:
        clause = f"exercise the {n_buttons} primary action(s)"
        first_three = ", ".join(f"'{b}'" for b in buttons[:3])
        if first_three:
            clause += f" ({first_three})"
        pieces.append(clause)

    page_clause = f"On the page titled '{title}': " if title else "On this page: "
    return page_clause + " and ".join(pieces) + "."


def _derive_feature_name(snap: dict, session_id: str) -> str:
    """Slugify the page title for use as a feature name; fall back to a
    deterministic id-based name if the title is empty or punctuation-only.
    """
    title = (snap.get("title") or "").strip()
    if title:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        if slug:
            return slug[:60]
    return f"browser_session_{session_id[-8:]}"


# Element name patterns that should never be clicked in read-only mode.
_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(delete|remove|destroy|drop|wipe|clear all|"
    r"pay|purchase|buy now|checkout|"
    r"send|publish|post|share with|"
    r"confirm purchase|confirm payment|confirm delete|confirm transfer|"
    r"unsubscribe|cancel subscription|"
    r"revoke|deactivate|disable account|close account)\b",
    re.IGNORECASE,
)


def _tool_specs(value_template_names: list[str]) -> list[Tool]:
    return [
        Tool(
            name="navigate",
            description="Load a URL in the browser. Use the starting URL first; only navigate to other URLs that you saw as links in a snapshot.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Absolute URL to load."}},
                "required": ["url"],
            },
        ),
        Tool(
            name="snapshot",
            description="Take a fresh snapshot of the current page. Returns the URL, title, and a list of every visible interactive element with a `ref` ID. You MUST call this before any click or type if the page has changed.",
            input_schema={"type": "object", "properties": {}},
        ),
        Tool(
            name="click",
            description="Click an element by its `ref` from the latest snapshot.",
            input_schema={
                "type": "object",
                "properties": {"ref": {"type": "string", "description": "ref ID from the latest snapshot, e.g. 'r3'"}},
                "required": ["ref"],
            },
        ),
        Tool(
            name="type",
            description="Type a value into a field by its `ref`. You do NOT type raw strings — pick a `value_template` name from the allowed list and the system generates a real value.",
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "value_template": {"type": "string", "enum": value_template_names},
                },
                "required": ["ref", "value_template"],
            },
        ),
        Tool(
            name="screenshot",
            description="Capture a screenshot of the current viewport for evidence. Use sparingly — only when you've reached a notable state (e.g. a form's success or error screen).",
            input_schema={"type": "object", "properties": {}},
        ),
        Tool(
            name="done",
            description="Signal that the exploration goal is covered (or that you cannot make further progress within the budget). Provide a one-sentence reason.",
            input_schema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        ),
    ]


class Orchestrator:
    def __init__(
        self,
        *,
        driver: BrowserDriver,
        ledger: ExplorationLedger,
        budget: Budget,
        settings: Settings,
        model_id: str,
        screenshot_dir: str,
        read_only: bool = True,
        on_progress: Callable[[ExplorationLedger], Awaitable[None]] | None = None,
    ) -> None:
        self.driver = driver
        self.ledger = ledger
        self.budget = budget
        self.settings = settings
        self.model_id = model_id
        self.screenshot_dir = screenshot_dir
        self.read_only = read_only
        self.on_progress = on_progress

        # Latest snapshot served to the LLM. Refs only valid against this.
        self._snapshot: dict | None = None
        self._current_page_id: str | None = None

        os.makedirs(screenshot_dir, exist_ok=True)

    # --- Public entry point ---

    async def run(self, *, max_turns: int = 80) -> ToolLoopResult:
        await self.driver.start()
        try:
            await self._maybe_derive_goal_and_feature()

            tools = _tool_specs(list(TEMPLATES.keys()))
            user_msg = explorer_user_message(
                goal=self.ledger.goal,
                starting_url=self.ledger.starting_url,
                value_templates=list(TEMPLATES.keys()),
            )
            try:
                result = await run_tool_loop(
                    system=EXPLORER_SYSTEM_PROMPT,
                    user=user_msg,
                    tools=tools,
                    on_tool_call=self._dispatch,
                    settings=self.settings,
                    budget=self.budget,
                    model_id=self.model_id,
                    max_turns=max_turns,
                    done_tool_name="done",
                )
            except BudgetExceeded as e:
                result = ToolLoopResult(
                    stopped="budget", final_text="", turns=0, last_tool=None, error=e.reason
                )

            stop_reason = result.stopped if not result.error else f"{result.stopped}:{result.error}"
            self.ledger.finalize(stop_reason=stop_reason)
            await self._notify_progress()
            return result
        finally:
            try:
                await self.driver.close()
            except Exception:
                logger.exception("driver.close failed")

    # --- Goal / feature_name derivation (Task 15) ---

    async def _maybe_derive_goal_and_feature(self) -> None:
        """When the explore caller didn't provide a goal/feature_name, take
        an explicit first navigate+snapshot, derive both from the page, and
        persist them. No-op when goal is already set; records goal_source
        for traceability either way.
        """
        goal_was_empty = not (self.ledger.goal or "").strip()

        derived_meta: dict[str, Any] = {
            "goal_source": "auto" if goal_was_empty else "user",
        }

        if goal_was_empty:
            # Need a snapshot to derive from. Do an explicit navigate first.
            try:
                await self.driver.navigate(self.ledger.starting_url)
                first_snap = await self.driver.snapshot()
                self._snapshot = first_snap
            except Exception as e:
                # Couldn't take the first snapshot — leave goal empty (user
                # message will still get a starting URL). Persist what we
                # know and let the loop continue or stop on its own.
                logger.exception("derivation pre-snapshot failed: %s", e)
                derived_meta["goal_source"] = "auto_failed"
                async with get_db() as _db:
                    await bs_service.set_metadata(_db, self.ledger.session_id, derived_meta)
                    await _db.commit()
                return

            derived_goal = _derive_goal_from_snapshot(first_snap)
            self.ledger.goal = derived_goal
            derived_meta["goal"] = derived_goal

        # Feature_name derivation: only if the session row has empty
        # feature_name. Needs a snapshot — reuse self._snapshot if we took
        # one, otherwise we can't derive (only happens when caller gave a
        # goal but no feature_name; rare).
        async with get_db() as _db:
            session = await bs_service.get_session(_db, self.ledger.session_id)
            if session and not (session.feature_name or "").strip() and self._snapshot is not None:
                fname = _derive_feature_name(self._snapshot, self.ledger.session_id)
                await bs_service.update_feature_name(_db, self.ledger.session_id, fname)
                derived_meta["feature_name"] = fname

            await bs_service.set_metadata(_db, self.ledger.session_id, derived_meta)
            await _db.commit()

    # --- Tool dispatcher ---

    async def _dispatch(self, name: str, input: dict[str, Any]) -> str:
        try:
            if name == "navigate":
                return await self._tool_navigate(str(input.get("url") or ""))
            if name == "snapshot":
                return await self._tool_snapshot()
            if name == "click":
                return await self._tool_click(str(input.get("ref") or ""))
            if name == "type":
                return await self._tool_type(
                    ref=str(input.get("ref") or ""),
                    value_template=str(input.get("value_template") or ""),
                )
            if name == "screenshot":
                return await self._tool_screenshot()
            return f"ERROR: unknown tool {name!r}"
        finally:
            await self._notify_progress()

    # --- Individual tool implementations ---

    async def _tool_navigate(self, url: str) -> str:
        if not url:
            return "ERROR: 'url' is required"
        try:
            await self.driver.navigate(url)
        except PermissionError as e:
            return f"BLOCKED: {e}"
        except Exception as e:
            return f"ERROR: navigate failed: {e}"

        snap = await self._take_and_record_snapshot()
        return self._format_for_llm(snap, status=f"navigated to {url}")

    async def _tool_snapshot(self) -> str:
        snap = await self._take_and_record_snapshot()
        return self._format_for_llm(snap, status="snapshot taken")

    async def _tool_click(self, ref: str) -> str:
        if self._snapshot is None:
            return "ERROR: no snapshot yet — call snapshot() first"
        target = self._find_element(ref)
        if not target:
            return (
                f"ERROR: ref {ref!r} is not in the latest snapshot. "
                "The page may have changed — call snapshot() to get fresh refs."
            )

        if self.read_only and self._is_destructive(target):
            self.ledger.add_action(
                type="click",
                target=_target_dict(ref, target),
                from_page=self._current_page_id or "",
                to_page=None,
                outcome="read_only_blocked",
            )
            return (
                f"BLOCKED: read-only mode forbids clicking {target.get('name')!r} "
                "(matches destructive pattern). Skip this and continue with non-destructive actions."
            )

        before_url = await self.driver.current_url()
        before_hash = self._current_snapshot_hash()
        before_page = self._current_page_id or ""

        try:
            await self.driver.click(ref)
        except RefNotFoundError as e:
            return f"ERROR: {e}. Call snapshot() to refresh refs."
        except Exception as e:
            return f"ERROR: click failed: {e}"

        try:
            self.budget.record_action()
        except BudgetExceeded:
            raise

        snap = await self._take_and_record_snapshot()
        after_url = snap.get("url") or ""
        after_hash = self._current_snapshot_hash()

        if before_url == after_url and before_hash == after_hash:
            outcome = "no_op"
            to_page: str | None = None
        elif before_url != after_url:
            outcome = "navigated"
            to_page = self._current_page_id
        else:
            outcome = "state_changed"
            to_page = self._current_page_id

        action_id = self.ledger.add_action(
            type="click",
            target=_target_dict(ref, target),
            from_page=before_page,
            to_page=to_page,
            outcome=outcome,
        )

        # Detect new errors visible after this click → record them as evidence.
        await self._scrape_errors(triggered_by=action_id)

        return self._format_for_llm(snap, status=f"click {outcome}")

    async def _tool_type(self, *, ref: str, value_template: str) -> str:
        if self._snapshot is None:
            return "ERROR: no snapshot yet — call snapshot() first"
        target = self._find_element(ref)
        if not target:
            return (
                f"ERROR: ref {ref!r} is not in the latest snapshot. "
                "Call snapshot() for fresh refs."
            )
        if value_template not in TEMPLATES:
            sample = ", ".join(list(TEMPLATES.keys())[:10])
            return f"ERROR: unknown value_template {value_template!r}. Valid examples: {sample} (and more)."

        # Deterministic per-(template, ref) so re-runs of the same exploration
        # produce reproducible inputs.
        seed = abs(hash((ref, value_template))) % 100000
        actual = gen_value(value_template, seed=seed)

        try:
            await self.driver.type(ref, actual)
        except RefNotFoundError as e:
            return f"ERROR: {e}. Call snapshot() to refresh refs."
        except Exception as e:
            return f"ERROR: type failed: {e}"

        try:
            self.budget.record_action()
        except BudgetExceeded:
            raise

        snap = await self._take_and_record_snapshot()
        action_id = self.ledger.add_action(
            type="type",
            target=_target_dict(ref, target),
            from_page=self._current_page_id or "",
            to_page=self._current_page_id,
            outcome="input_accepted",
            value_template=value_template,
            value_used=actual,
        )
        await self._scrape_errors(triggered_by=action_id)
        # Truncate value display so we don't leak huge strings into context.
        shown = actual if len(actual) <= 60 else actual[:60] + "…"
        return (
            f"typed value_template={value_template!r} (resolved to {shown!r}) into "
            f"{target.get('role')} {target.get('name')!r}\n"
            + self._format_for_llm(snap)
        )

    async def _tool_screenshot(self) -> str:
        if self._current_page_id is None:
            return "ERROR: no page in ledger yet — navigate or snapshot first"
        path = self._screenshot_path()
        try:
            await self.driver.screenshot(path)
        except Exception as e:
            return f"ERROR: screenshot failed: {e}"
        sid = self.ledger.add_screenshot(page=self._current_page_id, file_path=path)
        return f"screenshot captured as {sid} → {path}"

    # --- Internal helpers ---

    async def _take_and_record_snapshot(self) -> dict:
        snap = await self.driver.snapshot()
        self._snapshot = dict(snap)
        h = hash_snapshot(self._snapshot.get("text_dump") or "")
        existing = self.ledger.find_page_by_hash(h)
        if existing:
            self._current_page_id = existing
        else:
            # New page — capture an evidence screenshot too.
            sid: str | None = None
            try:
                path = self._screenshot_path()
                await self.driver.screenshot(path)
                sid = self.ledger.add_screenshot(page="(pending)", file_path=path)
            except Exception:
                logger.exception("auto-screenshot failed")
            page_id = self.ledger.add_page(
                url=self._snapshot.get("url") or "",
                title=self._snapshot.get("title") or "",
                snapshot_summary=self._snapshot.get("summary") or "",
                snapshot_hash=h,
                screenshot_id=sid,
            )
            # Backfill the screenshot's `page` field now that we know it.
            if sid:
                for s in self.ledger.screenshots:
                    if s["id"] == sid:
                        s["page"] = page_id
                        break
            self._current_page_id = page_id
            try:
                self.budget.record_page()
            except BudgetExceeded:
                raise
        return self._snapshot

    def _current_snapshot_hash(self) -> str:
        if not self._snapshot:
            return ""
        return hash_snapshot(self._snapshot.get("text_dump") or "")

    def _find_element(self, ref: str) -> dict | None:
        if not self._snapshot:
            return None
        for el in self._snapshot.get("elements") or []:
            if el.get("ref") == ref:
                return el
        return None

    def _is_destructive(self, target: dict) -> bool:
        name = (target.get("name") or "").strip()
        if not name:
            return False
        return bool(_DESTRUCTIVE_PATTERN.search(name))

    def _screenshot_path(self) -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        n = len(self.ledger.screenshots) + 1
        return os.path.join(self.screenshot_dir, f"{ts}_{n:03d}.png")

    async def _scrape_errors(self, *, triggered_by: str) -> None:
        """Capture any visible error / alert elements on the current page so
        the Author can ground negative tests in the actual error copy.
        """
        if not self._snapshot:
            return
        for el in self._snapshot.get("elements") or []:
            role = (el.get("role") or "").lower()
            name = (el.get("name") or "").strip()
            text = (el.get("text") or "").strip()
            content = name or text
            if not content:
                continue
            # Heuristic: roles commonly used for inline error / status display.
            if role in {"alert", "status"} or _looks_like_error(content):
                self.ledger.add_error(
                    page=self._current_page_id or "",
                    selector=f"[data-tcg-ref='{el.get('ref')}']",
                    text=content,
                    color_hex=None,
                    triggered_by=triggered_by,
                )

    def _format_for_llm(self, snap: dict, *, status: str = "") -> str:
        lines: list[str] = []
        if status:
            lines.append(f"STATUS: {status}")
        lines.append(
            f"PAGE: {snap.get('title') or ''} ({snap.get('url') or ''}) [{self._current_page_id}]"
        )
        elements = snap.get("elements") or []
        if not elements:
            lines.append("(no interactive elements detected on this page)")
        else:
            lines.append(f"INTERACTIVE ELEMENTS ({len(elements)}):")
            for el in elements[:60]:
                disabled = " (disabled)" if el.get("disabled") else ""
                tid = f" testid={el['testid']}" if el.get("testid") else ""
                lines.append(
                    f"  [{el['ref']}] {el.get('role') or '?'}{disabled} \"{el.get('name') or ''}\"{tid}"
                )
            if len(elements) > 60:
                lines.append(f"  ... and {len(elements) - 60} more")
        snap_b = self.budget.snapshot()
        lines.append(
            f"BUDGET: {snap_b['actions']}/{snap_b['max_actions']} actions, "
            f"{snap_b['pages']}/{snap_b['max_pages']} pages, "
            f"{int(snap_b['elapsed_seconds'])}s/{snap_b['max_seconds']}s elapsed"
        )
        return "\n".join(lines)

    async def _notify_progress(self) -> None:
        if self.on_progress is not None:
            try:
                await self.on_progress(self.ledger)
            except Exception:
                logger.exception("on_progress hook raised")


# --- Module-level helpers ---

_ERROR_PHRASES = (
    "invalid", "required", "must", "incorrect", "cannot", "can't",
    "missing", "not found", "failed", "error", "wrong", "denied",
)


def _looks_like_error(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _ERROR_PHRASES) and len(t) < 240


def _target_dict(ref: str, el: dict) -> dict:
    return {
        "role": el.get("role"),
        "name": el.get("name"),
        "ref": ref,
        "testid": el.get("testid"),
    }
