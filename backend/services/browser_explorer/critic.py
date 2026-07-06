"""Pure-Python validation pass run after the Author LLM call.

The Author is instructed to emit ``evidence_refs: ["p1", "a3", ...]`` per
test case, and to inline-cite IDs in steps. The Critic resolves every
citation against the ``ExplorationLedger`` and drops any case whose
citations don't all resolve. This is the final guard against hallucinated
behavior.

No LLM. Just JSON inspection + set lookups. Fast, cheap, deterministic.
"""

from __future__ import annotations

import re

from backend.services.browser_explorer.ledger import ExplorationLedger


# Inline citation pattern in step text: "[a1]", "[p3]", "[a1, p2]"
_INLINE_CITE = re.compile(r"\[((?:[paefs]\d+(?:\s*,\s*)?)+)\]")


def _extract_inline_refs(text: str) -> list[str]:
    """Pull every "[xN]" citation out of free-form step / expected_result text."""
    refs: list[str] = []
    for match in _INLINE_CITE.findall(text or ""):
        for token in match.split(","):
            token = token.strip()
            if token:
                refs.append(token)
    return refs


def _collect_refs_from_case(case: dict) -> list[str]:
    """Combine declared evidence_refs with inline [xN] citations."""
    refs: list[str] = []
    declared = case.get("evidence_refs")
    if isinstance(declared, list):
        refs.extend(str(r).strip() for r in declared if str(r).strip())
    for step in case.get("steps") or []:
        refs.extend(_extract_inline_refs(str(step)))
    refs.extend(_extract_inline_refs(str(case.get("expected_result") or "")))
    refs.extend(_extract_inline_refs(str(case.get("preconditions") or "")))
    refs.extend(_extract_inline_refs(str(case.get("title") or "")))
    return refs


def validate_citations(
    cases: list[dict],
    ledger: ExplorationLedger,
    *,
    require_at_least_one: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Split cases into ``(kept, dropped)`` based on citation resolution.

    Each dropped item carries a ``_drop_reason`` field for debugging.
    Kept items are augmented with a ``_resolved_refs`` field that lists
    the IDs successfully resolved (used downstream to populate
    ``test_case.source_ref``).
    """
    known = ledger.known_ids()
    kept: list[dict] = []
    dropped: list[dict] = []
    for case in cases:
        refs = _collect_refs_from_case(case)
        if require_at_least_one and not refs:
            dropped.append({**case, "_drop_reason": "no evidence references in case"})
            continue
        unresolved = [r for r in refs if r not in known]
        if unresolved:
            dropped.append({**case, "_drop_reason": f"unresolved evidence ids: {unresolved}"})
            continue
        # Optional: enforce that a known ledger element name appears in the title.
        # This is a softer rule; we only warn, not drop, because legitimate
        # tests can describe behavior without naming a single element.
        kept_case = dict(case)
        kept_case["_resolved_refs"] = sorted(set(refs))
        kept.append(kept_case)
    return kept, dropped


def apply_to_test_cases(test_cases, ledger: ExplorationLedger) -> tuple[list, list[dict]]:
    """Filter ``list[TestCase]`` by citation resolution.

    Returns ``(kept_test_cases, dropped_dicts)``. Each kept TestCase is
    returned with its ``source_ref`` populated from resolved ledger refs
    (overwriting any existing source_ref, which is more useful for AI
    explorations than the generic parser-derived one).

    Inline citations come from step / expected_result / title / preconditions
    text using the ``[xN]`` pattern; we don't require a structured
    ``evidence_refs`` field on TestCase, so model schema doesn't change.
    """
    # Convert TestCase → dict for the existing dict-based validator. Doing
    # this without importing the TestCase model keeps this file independent
    # of the rest of the backend.
    case_dicts = []
    for tc in test_cases:
        if hasattr(tc, "model_dump"):
            case_dicts.append(tc.model_dump())
        elif isinstance(tc, dict):
            case_dicts.append(tc)
        else:
            case_dicts.append({"title": str(tc), "steps": []})

    kept_dicts, dropped_dicts = validate_citations(case_dicts, ledger)
    # Build a quick index of which original TestCase indices survived.
    kept_indices = []
    kept_by_index: dict[int, dict] = {}
    for i, kept in enumerate(kept_dicts):
        # The kept_dicts list is in the same order as case_dicts minus drops.
        # We need to re-find the original index by title — titles are unique
        # within a single LLM call (existing dedup ensures it).
        for j, original in enumerate(case_dicts):
            if j in kept_indices:
                continue
            if original.get("title") == kept.get("title") and original.get("steps") == kept.get("steps"):
                kept_indices.append(j)
                kept_by_index[j] = kept
                break

    kept_test_cases = []
    for j, kept in kept_by_index.items():
        original = test_cases[j]
        new_ref = critic_source_ref(kept, ledger)
        if hasattr(original, "model_copy"):
            kept_test_cases.append(original.model_copy(update={"source_ref": new_ref}))
        else:
            updated = dict(original)
            updated["source_ref"] = new_ref
            kept_test_cases.append(updated)

    return kept_test_cases, dropped_dicts


def critic_source_ref(case: dict, ledger: ExplorationLedger) -> str:
    """Build a concrete traceability string from resolved refs.

    Used to populate ``TestCase.source_ref`` so each persisted case carries
    a pointer back to the page URL(s) and screenshot(s) that justified it.
    """
    refs = case.get("_resolved_refs") or []
    by_id: dict[str, dict] = {}
    for collection in (
        ledger.pages,
        ledger.actions,
        ledger.errors_observed,
        ledger.screenshots,
    ):
        for item in collection:
            by_id[item["id"]] = item

    parts: list[str] = []
    seen_urls: set[str] = set()
    seen_shots: set[str] = set()
    for r in refs:
        item = by_id.get(r)
        if not item:
            continue
        if r.startswith("p"):
            url = item.get("url")
            if url and url not in seen_urls:
                parts.append(f"page:{url}")
                seen_urls.add(url)
        elif r.startswith("a"):
            target = item.get("target") or {}
            n = target.get("name")
            if n:
                parts.append(f"action:{item['type']} '{n}'")
        elif r.startswith("e"):
            t = item.get("text")
            if t:
                parts.append(f'error:"{t[:80]}"')
        elif r.startswith("s"):
            fp = item.get("file_path")
            if fp and fp not in seen_shots:
                parts.append(f"screenshot:{fp}")
                seen_shots.add(fp)
    out = " | ".join(parts) or f"browser_explore:{ledger.session_id}"
    return out[:2000]
