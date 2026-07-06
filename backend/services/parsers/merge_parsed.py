"""Merge multiple ParsedInput instances for a single LLM generation call."""

from __future__ import annotations

from backend.services.parsers.base import ParsedInput
from backend.services.source_ref import derive_source_ref


def _dedupe_preserve(xs: list[str], cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        s = (x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def merge_parsed_inputs(parsed_list: list[ParsedInput], target_feature_name: str) -> ParsedInput:
    """Concatenate structured lists and raw_context sections; set source_type to multi."""
    if not parsed_list:
        raise ValueError("parsed_list must not be empty")
    if len(parsed_list) == 1:
        p = parsed_list[0]
        fn = target_feature_name if (p.feature_name or "").strip() else target_feature_name
        return p.model_copy(update={"feature_name": fn})

    parts: list[str] = []
    screens: list[str] = []
    ui_el: list[str] = []
    actions: list[str] = []
    rules: list[str] = []
    meta_sources: list[dict] = []

    for i, p in enumerate(parsed_list):
        parts.append(f"=== Source {i + 1}: {p.source_type} ===\n{p.raw_context}".strip())
        screens.extend(p.screens)
        ui_el.extend(p.ui_elements)
        actions.extend(p.user_actions)
        rules.extend(p.business_rules)
        # Carry full sub-source metadata (incl. underscore-prefixed sidechannels like
        # _image_bytes) plus feature_name and raw_context so the router can fan out
        # generation_inputs rows with the right per-source content/images.
        meta_sources.append({
            "source_type": p.source_type,
            "metadata": dict(p.metadata or {}),
            "feature_name": p.feature_name,
            "raw_context": p.raw_context,
        })

    refs = [derive_source_ref(p) for p in parsed_list]
    combined_ref = " | ".join(refs)[:2000]

    return ParsedInput(
        source_type="multi",
        feature_name=target_feature_name,
        screens=_dedupe_preserve(screens, 200),
        ui_elements=_dedupe_preserve(ui_el, 300),
        user_actions=_dedupe_preserve(actions, 200),
        business_rules=_dedupe_preserve(rules, 120),
        raw_context="\n\n".join(parts),
        metadata={
            "merged": True,
            "sources": meta_sources,
            "source_refs": refs,
            "combined_source_ref": combined_ref,
        },
    )
