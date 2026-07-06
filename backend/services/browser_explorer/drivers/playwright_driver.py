"""Playwright-backed BrowserDriver.

Snapshots the page, marks every interactive element with a ``data-tcg-ref``
attribute, and serves subsequent click/type calls by ref. This makes refs
cheap to validate (the marker is on the element until the next snapshot
overwrites it) and avoids fragile CSS-selector heuristics on the LLM side.

We deliberately use a small, stable element-discovery query rather than the
full accessibility tree — full a11y trees are noisy and include landmarks,
groups, and decoration that are useless for testing actions.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.browser_explorer.drivers import RefNotFoundError, Snapshot

logger = logging.getLogger(__name__)


# Selectors for "things a test would interact with". Order matters: refs are
# assigned in document order across this combined query.
_INTERACTIVE_SELECTOR = ", ".join(
    [
        "button",
        "a[href]",
        "input:not([type=hidden])",
        "textarea",
        "select",
        "[role=button]",
        "[role=link]",
        "[role=textbox]",
        "[role=checkbox]",
        "[role=radio]",
        "[role=switch]",
        "[role=tab]",
        "[role=menuitem]",
        "[role=combobox]",
        "[role=option]",
        "label[for]",
        "[contenteditable=true]",
    ]
)


# JS executed in the page to mark elements and return their structured info.
# Returns: { url, title, elements: [{ref, role, name, tag, testid, text, disabled, type}], text_dump, summary }
_SNAPSHOT_JS = r"""
(selector) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 200);

  const computeAccName = (el) => {
    if (!el) return "";
    const aria = el.getAttribute("aria-label");
    if (aria) return norm(aria);
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const ids = labelledby.split(/\s+/);
      const txt = ids.map(id => (document.getElementById(id) || {}).textContent || "").join(" ");
      if (txt.trim()) return norm(txt);
    }
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") {
      if (el.id) {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl && lbl.textContent) return norm(lbl.textContent);
      }
      const wrap = el.closest("label");
      if (wrap) return norm(wrap.textContent);
      const ph = el.getAttribute("placeholder");
      if (ph) return norm(ph);
      const nm = el.getAttribute("name");
      if (nm) return norm(nm);
    }
    if (el.tagName === "IMG") {
      const alt = el.getAttribute("alt");
      if (alt) return norm(alt);
    }
    return norm(el.textContent || "");
  };

  const computeRole = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.toLowerCase();
    const t = el.tagName.toLowerCase();
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "button") return "button";
    if (t === "select") return "combobox";
    if (t === "textarea") return "textbox";
    if (t === "input") {
      const it = (el.getAttribute("type") || "text").toLowerCase();
      if (["text","email","tel","url","search","password","number"].includes(it)) return "textbox";
      if (it === "checkbox") return "checkbox";
      if (it === "radio") return "radio";
      if (it === "submit" || it === "button" || it === "reset") return "button";
      return "textbox";
    }
    if (t === "label") return "label";
    return t;
  };

  const isVisible = (el) => {
    if (!el) return false;
    if (el.hidden) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
    return true;
  };

  // Clear previous markers so refs are stable per-snapshot.
  document.querySelectorAll("[data-tcg-ref]").forEach(el => el.removeAttribute("data-tcg-ref"));

  const nodes = Array.from(document.querySelectorAll(selector)).filter(isVisible);

  const elements = [];
  let next = 1;
  for (const el of nodes) {
    const ref = `r${next++}`;
    el.setAttribute("data-tcg-ref", ref);
    const role = computeRole(el);
    const name = computeAccName(el);
    const tag = el.tagName.toLowerCase();
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-cy") || null;
    const text = norm(el.textContent || "");
    const disabled = el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true";
    const type = el.getAttribute("type") || null;
    elements.push({ ref, role, name, tag, testid, text, disabled, type });
  }

  // Group sentinel: capture the headings on the page to give the LLM a sense
  // of structure without dumping the full a11y tree.
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, [role=heading]"))
    .filter(isVisible)
    .map(h => `${(h.tagName || "").toLowerCase()}: ${norm(h.textContent || "")}`)
    .slice(0, 25);

  const text_dump = [
    `url:${location.href}`,
    `title:${document.title}`,
    ...headings.map(h => `H ${h}`),
    ...elements.map(e => `${e.role}|${e.name}|${e.testid || ""}|${e.disabled ? "d" : ""}`),
  ].join("\n");

  const summary_lines = [
    ...headings.slice(0, 8).map(h => `  ${h}`),
    ...elements.slice(0, 60).map(e => `  [${e.ref}] ${e.role}${e.disabled ? "(disabled)" : ""} "${e.name}"${e.testid ? ` testid=${e.testid}` : ""}`),
  ];
  const summary = summary_lines.join("\n");

  return {
    url: location.href,
    title: document.title,
    elements,
    text_dump,
    summary,
  };
}
"""


class PlaywrightDriver:
    """BrowserDriver implementation using Playwright async API."""

    def __init__(self, *, headless: bool = True, host_allowlist: list[str] | None = None) -> None:
        self._headless = headless
        self._host_allowlist = host_allowlist
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        # Imported lazily so the rest of the codebase is importable even
        # before the user has run ``playwright install``.
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(15000)

    async def navigate(self, url: str) -> None:
        if self._host_allowlist:
            from urllib.parse import urlparse

            host = urlparse(url).hostname or ""
            if not any(host == h or host.endswith("." + h) for h in self._host_allowlist):
                raise PermissionError(f"navigation to {host!r} not in allowlist")
        await self._page.goto(url, wait_until="domcontentloaded")
        # Brief settle to let SPAs render their first paint.
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

    async def snapshot(self) -> Snapshot:
        result = await self._page.evaluate(_SNAPSHOT_JS, _INTERACTIVE_SELECTOR)
        # ``elements`` is the full list with refs assigned in DOM order.
        return Snapshot(result)

    async def click(self, ref: str) -> dict[str, Any]:
        loc = self._page.locator(f'[data-tcg-ref="{ref}"]')
        try:
            count = await loc.count()
        except Exception as e:
            raise RefNotFoundError(f"ref {ref!r} not addressable: {e}") from e
        if count == 0:
            raise RefNotFoundError(f"ref {ref!r} not present in current DOM")
        await loc.first.click()
        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return {"ok": True}

    async def type(self, ref: str, value: str) -> dict[str, Any]:
        loc = self._page.locator(f'[data-tcg-ref="{ref}"]')
        if await loc.count() == 0:
            raise RefNotFoundError(f"ref {ref!r} not present in current DOM")
        await loc.first.fill(value)
        return {"ok": True}

    async def screenshot(self, path: str) -> str:
        await self._page.screenshot(path=path, full_page=False)
        return path

    async def current_url(self) -> str:
        return self._page.url if self._page else ""

    async def page_title(self) -> str:
        try:
            return await self._page.title()
        except Exception:
            return ""

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            logger.exception("error closing context")
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            logger.exception("error closing browser")
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            logger.exception("error stopping playwright")
