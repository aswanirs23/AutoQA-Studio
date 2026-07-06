"""Derive human-readable ``source_ref`` strings for test cases from parser output.

Used for traceability in the UI and exports (e.g. ``Jira: PROJ-123``, Figma URL, ``Multi`` sources).
"""

from __future__ import annotations

from backend.services.parsers.base import ParsedInput


def derive_source_ref(parsed: ParsedInput) -> str:
    """Short string for audit / export (e.g. Jira: PROJ-123, Figma URL)."""
    meta = parsed.metadata or {}
    st = (parsed.source_type or "").lower()

    if st == "jira":
        key = str(meta.get("issue_key") or "").strip()
        return f"Jira: {key}" if key else "Jira"

    if st == "figma":
        url = str(meta.get("url") or "").strip()
        if url:
            return url[:500]
        fk = str(meta.get("file_key") or "").strip()
        return f"Figma: {fk}" if fk else "Figma"

    if st == "screenshot":
        return "Screenshot upload"

    if st == "browser_session":
        if meta.get("mode") == "ai_explore":
            goal = str(meta.get("goal") or "").strip()
            url = str(meta.get("url") or "").strip()
            if goal and url:
                return f"AI explore: {goal} @ {url}"[:500]
            if url:
                return f"AI explore @ {url}"[:500]
            return "AI explore"
        sid = str(meta.get("session_id") or "").strip()
        return f"Browser session: {sid}" if sid else "Browser session"

    if st == "text":
        fn = str(meta.get("feature_name") or parsed.feature_name or "").strip()
        if fn:
            return f"Manual text ({fn})"
        return "Manual text"

    if st == "multi":
        cr = str(meta.get("combined_source_ref") or "").strip()
        if cr:
            return cr[:2000]
        refs = meta.get("source_refs")
        if isinstance(refs, list) and refs:
            return " | ".join(str(x) for x in refs)[:2000]
        return "Multiple sources"

    return st or "unknown"


def derive_generation_summary(parsed: ParsedInput) -> str:
    """One-line label for the Generations panel card heading.

    Single-source cases produce a short type-specific label; multi cases summarize counts.
    """
    meta = parsed.metadata or {}
    st = (parsed.source_type or "").lower()

    if st == "jira":
        key = str(meta.get("issue_key") or "").strip()
        return f"Jira: {key}" if key else "Jira"

    if st == "figma":
        name = str(meta.get("file_name") or meta.get("frame_name") or "").strip()
        return f"Figma — {name}" if name else "Figma"

    if st == "text":
        fn = str(meta.get("feature_name") or parsed.feature_name or "").strip()
        return f"Manual text ({fn})" if fn else "Manual text"

    if st == "screenshot":
        fn = str(meta.get("filename") or "").strip()
        return f"Screenshot — {fn}" if fn else "Screenshot"

    if st == "browser_session":
        goal = str(meta.get("goal") or "").strip()
        url = str(meta.get("url") or "").strip()
        if goal:
            return f"Browser session: {goal}"[:160]
        if url:
            return f"Browser session @ {url}"[:160]
        return "Browser session"

    if st == "multi":
        sources = meta.get("sources") or []
        counts: dict[str, int] = {}
        order: list[str] = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            t = str(s.get("source_type") or "").lower()
            if not t:
                continue
            if t not in counts:
                order.append(t)
            counts[t] = counts.get(t, 0) + 1
        labels = {
            "jira": "Jira",
            "figma": "Figma",
            "screenshot": "Screenshot",
            "text": "Manual text",
            "browser_session": "Browser session",
        }
        parts: list[str] = []
        for t in order:
            label = labels.get(t, t)
            n = counts[t]
            parts.append(f"{n} {label}{'s' if n > 1 else ''}" if n > 1 else label)
        return " + ".join(parts) if parts else "Multiple sources"

    return st or "unknown"
