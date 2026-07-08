"""LLM abstraction: OpenAI, Anthropic, and Google Gemini.

Flow:
1. ``prompts/templates.py`` builds the user message (parsed input + existing cases, or iterate prompt).
2. ``_complete_json`` picks provider (UI override or ``settings.llm_provider``); uses ``get_effective_settings()`` for API keys.
3. Model returns a JSON object string; we parse and map to ``TestCase`` list (with JSON repair / retry on failure).
4. ``TestCase.id`` is a placeholder here; the DB layer assigns real ``TC_*`` ids when saving.

OpenAI uses JSON mode; Gemini uses ``response_mime_type`` JSON; Anthropic is instructed to emit raw JSON only.
Gemini calls run in ``asyncio.to_thread`` so the event loop is not blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from backend.config import Settings, effective_llm_provider, get_effective_settings, resolved_model_id
from backend.models.test_case import TestCase
from backend.prompts.templates import SYSTEM_PROMPT, build_generation_user_message, build_iterate_user_message
from backend.services.llm_usage import log_anthropic_usage, log_gemini_usage, log_openai_usage
from backend.services.parsers.base import ParsedInput

logger = logging.getLogger(__name__)

VALID_TEST_TYPES = frozenset(
    {
        "happy",
        "edge",
        "negative",
        "smoke",
        "regression",
        "integration",
        "api",
        "security",
        "accessibility",
        "performance",
        "boundary",
        "usability",
    }
)


def _normalize_type(raw: str) -> str:
    t = (raw or "happy").strip().lower()
    if t not in VALID_TEST_TYPES:
        logger.warning("LLM returned unknown test type %r; defaulting to happy", raw)
        return "happy"
    return t


def _try_repair_json_text(text: str) -> str:
    """Best-effort fixes for common LLM JSON mistakes."""
    t = text.strip()
    # Remove trailing commas before } or ]
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    # If still not balanced, try to find outermost JSON object
    if not t.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            t = m.group(0)
    return t


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _try_repair_json_text(text)
        return json.loads(repaired)


def _parse_json_with_fallback(text: str) -> dict[str, Any]:
    """Parse JSON; on failure try repair and one more load."""
    try:
        return _parse_json_object(text)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s; attempting repair", e)
        try:
            repaired = _try_repair_json_text(
                re.sub(r"```(?:json)?\s*([\s\S]*?)```", lambda m: m.group(1), text.strip())
                if "```" in text
                else text
            )
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise


def _items_to_test_cases(items: list[dict[str, Any]]) -> list[TestCase]:
    out: list[TestCase] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or f"Test case {i+1}").strip()
        feature = str(row.get("feature") or "general").strip()
        typ = _normalize_type(str(row.get("type") or "happy"))
        steps = row.get("steps") or []
        if not isinstance(steps, list):
            steps = [str(steps)]
        else:
            steps = [str(s) for s in steps]
        out.append(
            TestCase(
                id="pending",
                title=title,
                feature=feature,
                type=typ,
                preconditions=str(row.get("preconditions") or ""),
                steps=steps,
                expected_result=str(row.get("expected_result") or ""),
                priority=str(row.get("priority") or "medium"),
            )
        )
    return out


async def generate_from_parsed(
    parsed: ParsedInput,
    existing: list[TestCase],
    settings: Settings | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    extra_instruction: str | None = None,
    project_description: str = "",
    target_feature_name: str = "",
    min_test_cases: int | None = None,
    preferred_test_types: list[str] | None = None,
) -> list[TestCase]:
    settings = settings or get_effective_settings()
    user_msg = build_generation_user_message(
        parsed,
        existing,
        project_description=project_description,
        target_feature_name=target_feature_name,
        extra_instruction=extra_instruction,
        min_test_cases=min_test_cases,
        preferred_test_types=preferred_test_types,
    )
    raw = await _complete_json(SYSTEM_PROMPT, user_msg, settings, provider_override, model_override)
    data = await _parse_json_loop(user_msg, raw, settings, provider_override, model_override)
    items = data.get("test_cases") or data.get("cases") or []
    if not isinstance(items, list):
        items = []
    return _items_to_test_cases(items)


async def generate_iterate(
    existing: list[TestCase],
    instruction: str,
    feature_filter: str | None,
    type_filter: str | None,
    settings: Settings | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    project_description: str = "",
    min_test_cases: int | None = None,
    preferred_test_types: list[str] | None = None,
) -> list[TestCase]:
    settings = settings or get_effective_settings()
    user_msg = build_iterate_user_message(
        existing,
        instruction,
        feature_filter,
        type_filter,
        project_description=project_description,
        min_test_cases=min_test_cases,
        preferred_test_types=preferred_test_types,
    )
    raw = await _complete_json(SYSTEM_PROMPT, user_msg, settings, provider_override, model_override)
    data = await _parse_json_loop(user_msg, raw, settings, provider_override, model_override)
    items = data.get("test_cases") or []
    if not isinstance(items, list):
        items = []
    return _items_to_test_cases(items)


async def _parse_json_loop(
    user_msg: str,
    raw: str,
    settings: Settings,
    provider_override: str | None,
    model_override: str | None,
) -> dict[str, Any]:
    try:
        return _parse_json_with_fallback(raw)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed (%s); retrying LLM with stricter JSON instruction", e)
        retry_user = (
            user_msg
            + "\n\nCRITICAL: Respond with a single valid JSON object only. "
            'The root object must contain key "test_cases" whose value is an array of objects. '
            "No markdown, no commentary, no trailing commas."
        )
        raw2 = await _complete_json(SYSTEM_PROMPT, retry_user, settings, provider_override, model_override)
        return _parse_json_with_fallback(raw2)


async def _complete_json(
    system: str,
    user: str,
    settings: Settings,
    provider_override: str | None,
    model_override: str | None = None,
) -> str:
    provider = effective_llm_provider(settings, provider_override)
    model_id = resolved_model_id(settings, provider, model_override)
    if provider == "anthropic":
        return await _anthropic_complete(system, user, settings, model_id)
    if provider == "gemini":
        return await _gemini_complete(system, user, settings, model_id)
    return await _openai_complete(system, user, settings, model_id)


async def _openai_complete(system: str, user: str, settings: Settings, model_id: str) -> str:
    if not settings.openai_api_key:
        raise ValueError("OpenAI API key is not set — add it in Settings")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    log_openai_usage(resp, op="json_complete", model=model_id)
    return resp.choices[0].message.content or "{}"


async def _anthropic_complete(system: str, user: str, settings: Settings, model_id: str) -> str:
    if not settings.anthropic_api_key:
        raise ValueError("Anthropic API key is not set — add it in Settings")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=model_id,
        max_tokens=8192,
        system=system + " Always respond with a single JSON object only, no markdown.",
        messages=[{"role": "user", "content": user}],
    )
    log_anthropic_usage(msg, op="json_complete", model=model_id)
    parts: list[str] = []
    for b in msg.content:
        if hasattr(b, "text"):
            parts.append(b.text)
    return "".join(parts) or "{}"


async def _gemini_complete(system: str, user: str, settings: Settings, model_id: str) -> str:
    if not settings.gemini_api_key:
        raise ValueError("Google Gemini API key is not set — add it in Settings")

    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    gen_model = genai.GenerativeModel(
        model_id,
        system_instruction=system,
    )

    def _sync_call() -> str:
        response = gen_model.generate_content(
            user,
            generation_config=genai.GenerationConfig(
                temperature=0.4,
                response_mime_type="application/json",
            ),
        )
        log_gemini_usage(response, op="json_complete", model=model_id)
        return response.text or "{}"

    return await asyncio.to_thread(_sync_call)


async def generate_playwright_code(
    tc_dict: dict,
    base_url: str,
    settings: Settings,
    provider_override: str | None = None,
    model_override: str | None = None,
    *,
    is_login: bool = False,
    landing_path: str = "",
    has_credentials: bool = False,
) -> str:
    """Call the LLM to translate a manual test case into Playwright Python code.

    Returns the raw code string (no markdown fences, no commentary).
    Unlike the JSON-generation flows, this function bypasses the JSON-forcing
    provider helpers and calls each provider's API directly, asking for plain
    Python source.

    Raises ValueError if the configured LLM returns an empty or invalid response.
    """
    from backend.prompts.templates import PLAYWRIGHT_SYSTEM_PROMPT, build_playwright_user_message

    user = build_playwright_user_message(
        tc_dict, base_url, is_login=is_login, landing_path=landing_path, has_credentials=has_credentials
    )
    provider = effective_llm_provider(settings, provider_override)
    model_id = resolved_model_id(settings, provider, model_override)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("Anthropic API key is not set — add it in Settings")
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        msg = await client.messages.create(
            model=model_id,
            max_tokens=8192,
            system=PLAYWRIGHT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        log_anthropic_usage(msg, op="playwright_code", model=model_id)
        parts: list[str] = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        raw = "".join(parts)
    elif provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("Google Gemini API key is not set — add it in Settings")
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        gen_model = genai.GenerativeModel(
            model_id,
            system_instruction=PLAYWRIGHT_SYSTEM_PROMPT,
        )

        def _sync_call() -> str:
            response = gen_model.generate_content(
                user,
                generation_config=genai.GenerationConfig(temperature=0.2),
            )
            log_gemini_usage(response, op="playwright_code", model=model_id)
            return response.text or ""

        raw = await asyncio.to_thread(_sync_call)
    else:
        # OpenAI: call directly without forcing JSON response_format (we want raw code)
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key is not set — add it in Settings")
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": PLAYWRIGHT_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        log_openai_usage(resp, op="playwright_code", model=model_id)
        raw = resp.choices[0].message.content or ""

    raw = raw.strip()
    # Strip markdown fences if the model included them despite instructions
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw or "async def test" not in raw:
        raise ValueError("LLM did not return a valid Playwright test function")
    return raw


async def suggest_expected_result(
    current_expected_result: str,
    actual_page_text: str,
    error_message: str,
    settings: Settings,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> str:
    """Call the LLM to rewrite a test case's expected_result based on observed app behavior.

    Returns the raw rewritten text (no markdown fences, no commentary).
    Raises ValueError if the configured LLM returns an empty response.
    """
    from backend.prompts.templates import (
        EXPECTED_RESULT_REWRITE_SYSTEM_PROMPT,
        build_expected_result_rewrite_user_message,
    )

    user = build_expected_result_rewrite_user_message(
        current_expected_result, actual_page_text, error_message
    )
    provider = effective_llm_provider(settings, provider_override)
    model_id = resolved_model_id(settings, provider, model_override)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("Anthropic API key is not set — add it in Settings")
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        msg = await client.messages.create(
            model=model_id,
            max_tokens=1024,
            system=EXPECTED_RESULT_REWRITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        log_anthropic_usage(msg, op="expected_result", model=model_id)
        parts: list[str] = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        raw = "\n".join(parts)
    elif provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("Google Gemini API key is not set — add it in Settings")
        import google.generativeai as genai
        import asyncio as _asyncio

        genai.configure(api_key=settings.gemini_api_key)
        gen_model = genai.GenerativeModel(model_id, system_instruction=EXPECTED_RESULT_REWRITE_SYSTEM_PROMPT)

        def _sync_call() -> str:
            response = gen_model.generate_content(
                user,
                generation_config=genai.GenerationConfig(temperature=0.3),
            )
            log_gemini_usage(response, op="expected_result", model=model_id)
            return response.text or ""

        raw = await _asyncio.to_thread(_sync_call)
    else:
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key is not set — add it in Settings")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": EXPECTED_RESULT_REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
        log_openai_usage(resp, op="expected_result", model=model_id)
        raw = resp.choices[0].message.content or ""

    raw = raw.strip()
    # Strip markdown fences if the model included them despite instructions
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw:
        raise ValueError("LLM returned an empty expected_result rewrite")
    return raw
