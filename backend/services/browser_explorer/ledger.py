"""Evidence Ledger — the structured record of what the explorer observed.

The orchestrator builds one of these as exploration runs. The Author LLM
phase consumes this object (and only this object) when writing test cases.
The Critic phase then validates that every citation in a generated test
case resolves to an entry here. This is the mechanism that prevents the
authoring LLM from inventing UI it never saw.

Stored as JSON in ``BrowserSession.metadata.evidence_ledger``. Screenshots
are written to disk and referenced by ID; the ``data_url`` field is set
only briefly during capture and cleared before persistence to keep the
ledger small.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_snapshot(text: str) -> str:
    """Stable hash of an accessibility-tree dump for page-state dedup."""
    h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{h[:16]}"


class ExplorationLedger:
    """Mutable builder. Call ``to_dict()`` to serialize for persistence."""

    def __init__(self, session_id: str, goal: str, starting_url: str) -> None:
        self.session_id = session_id
        self.goal = goal
        self.starting_url = starting_url
        self.started_at = _now_iso()
        self.completed_at: str | None = None
        self.stop_reason: str | None = None

        self.pages: list[dict] = []
        self.actions: list[dict] = []
        self.forms: list[dict] = []
        self.errors_observed: list[dict] = []
        self.screenshots: list[dict] = []

        self._page_hashes: dict[str, str] = {}  # snapshot_hash -> page_id
        self._next_ids = {"p": 0, "a": 0, "f": 0, "e": 0, "s": 0}

    # --- ID minting ---

    def _next(self, prefix: str) -> str:
        self._next_ids[prefix] += 1
        return f"{prefix}{self._next_ids[prefix]}"

    # --- Page management with state-hash dedup ---

    def find_page_by_hash(self, snapshot_hash: str) -> str | None:
        return self._page_hashes.get(snapshot_hash)

    def add_page(
        self,
        *,
        url: str,
        title: str,
        snapshot_summary: str,
        snapshot_hash: str,
        screenshot_id: str | None = None,
    ) -> str:
        # Dedup: if we've seen this exact accessibility-tree hash, return the
        # existing page id rather than creating a duplicate.
        if existing := self._page_hashes.get(snapshot_hash):
            return existing
        page_id = self._next("p")
        self.pages.append(
            {
                "id": page_id,
                "url": url,
                "title": title,
                "snapshot_summary": snapshot_summary[:2000],
                "snapshot_hash": snapshot_hash,
                "screenshot_id": screenshot_id,
                "visited_at": _now_iso(),
            }
        )
        self._page_hashes[snapshot_hash] = page_id
        return page_id

    # --- Actions ---

    def add_action(
        self,
        *,
        type: str,
        target: dict,
        from_page: str,
        to_page: str | None = None,
        outcome: str = "ok",
        value_template: str | None = None,
        value_used: str | None = None,
    ) -> str:
        action_id = self._next("a")
        entry: dict[str, Any] = {
            "id": action_id,
            "type": type,
            "target": target,
            "from_page": from_page,
            "to_page": to_page,
            "outcome": outcome,
            "ts": _now_iso(),
        }
        if value_template is not None:
            entry["value_template"] = value_template
        if value_used is not None:
            entry["value_used"] = value_used
        self.actions.append(entry)
        return action_id

    # --- Forms (collections of related inputs + a submit) ---

    def add_form(
        self,
        *,
        page: str,
        name: str,
        fields: list[dict],
        submit_action_id: str | None = None,
    ) -> str:
        form_id = self._next("f")
        self.forms.append(
            {
                "id": form_id,
                "page": page,
                "name": name,
                "fields": fields,
                "submit_action_id": submit_action_id,
            }
        )
        return form_id

    # --- Errors observed (text + colour, grounded in real DOM) ---

    def add_error(
        self,
        *,
        page: str,
        selector: str,
        text: str,
        color_hex: str | None = None,
        triggered_by: str | None = None,
    ) -> str:
        err_id = self._next("e")
        self.errors_observed.append(
            {
                "id": err_id,
                "page": page,
                "selector": selector,
                "text": text[:500],
                "color_hex": color_hex,
                "triggered_by": triggered_by,
            }
        )
        return err_id

    # --- Screenshots (stored on disk; ledger holds path + id only) ---

    def add_screenshot(self, *, page: str, file_path: str) -> str:
        s_id = self._next("s")
        self.screenshots.append(
            {
                "id": s_id,
                "page": page,
                "ts": _now_iso(),
                "file_path": file_path,
            }
        )
        return s_id

    # --- Lookup helpers used by the Critic ---

    def known_ids(self) -> set[str]:
        ids: set[str] = set()
        for collection in (self.pages, self.actions, self.forms, self.errors_observed, self.screenshots):
            for item in collection:
                ids.add(item["id"])
        return ids

    def known_element_names(self) -> set[str]:
        """All accessibility names the Author is permitted to reference."""
        names: set[str] = set()
        for a in self.actions:
            t = a.get("target") or {}
            n = t.get("name")
            if n:
                names.add(n.strip())
        for f in self.forms:
            for fld in f.get("fields") or []:
                n = fld.get("name")
                if n:
                    names.add(n.strip())
            n = f.get("name")
            if n:
                names.add(n.strip())
        for e in self.errors_observed:
            t = e.get("text")
            if t:
                names.add(t.strip())
        return names

    # --- Finalize ---

    def finalize(self, stop_reason: str) -> None:
        self.completed_at = _now_iso()
        self.stop_reason = stop_reason

    def to_dict(self) -> dict:
        return {
            "ledger_version": 1,
            "session_id": self.session_id,
            "goal": self.goal,
            "starting_url": self.starting_url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stop_reason": self.stop_reason,
            "pages": self.pages,
            "actions": self.actions,
            "forms": self.forms,
            "errors_observed": self.errors_observed,
            "screenshots": self.screenshots,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExplorationLedger":
        led = cls(
            session_id=data.get("session_id", ""),
            goal=data.get("goal", ""),
            starting_url=data.get("starting_url", ""),
        )
        led.started_at = data.get("started_at", led.started_at)
        led.completed_at = data.get("completed_at")
        led.stop_reason = data.get("stop_reason")
        led.pages = list(data.get("pages") or [])
        led.actions = list(data.get("actions") or [])
        led.forms = list(data.get("forms") or [])
        led.errors_observed = list(data.get("errors_observed") or [])
        led.screenshots = list(data.get("screenshots") or [])
        for p in led.pages:
            if h := p.get("snapshot_hash"):
                led._page_hashes[h] = p["id"]
        for prefix in ("p", "a", "f", "e", "s"):
            collection = {
                "p": led.pages,
                "a": led.actions,
                "f": led.forms,
                "e": led.errors_observed,
                "s": led.screenshots,
            }[prefix]
            led._next_ids[prefix] = len(collection)
        return led
