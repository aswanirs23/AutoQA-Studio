"""Tests for prompt template rendering: PROJECT CONTEXT block + JSON body shape."""

from __future__ import annotations

import json

from backend.prompts.templates import (
    build_generation_user_message,
    build_iterate_user_message,
)
from backend.services.parsers.base import ParsedInput


def _make_parsed() -> ParsedInput:
    return ParsedInput(
        source_type="text",
        feature_name="Login",
        screens=[],
        ui_elements=[],
        user_actions=[],
        business_rules=[],
        raw_context="User can log in with email and password.",
        metadata={},
    )


def test_generation_prompt_includes_project_context_block_when_description_set():
    msg = build_generation_user_message(
        _make_parsed(),
        existing=[],
        project_description="HIPAA-regulated portal; every action must audit-log.",
        target_feature_name="Login",
    )
    assert "=== PROJECT CONTEXT (apply to every test case) ===" in msg
    assert "HIPAA-regulated portal; every action must audit-log." in msg
    assert "=== END PROJECT CONTEXT ===" in msg
    # PROJECT CONTEXT block must come before DESIGN CONTEXT block.
    assert msg.index("=== PROJECT CONTEXT") < msg.index("=== DESIGN CONTEXT")


def test_generation_prompt_uses_empty_sentinel_when_description_blank():
    msg = build_generation_user_message(
        _make_parsed(),
        existing=[],
        project_description="",
        target_feature_name="Login",
    )
    assert "=== PROJECT CONTEXT (apply to every test case) ===" in msg
    assert "(no project description provided)" in msg


def test_generation_prompt_drops_project_context_key_from_body():
    msg = build_generation_user_message(
        _make_parsed(),
        existing=[],
        project_description="anything",
        target_feature_name="Login",
    )
    # Extract the JSON body block (the only fenced-by-newlines JSON object in the message).
    start = msg.index("{")
    # Crude balanced scan
    depth = 0
    end = start
    for i, ch in enumerate(msg[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    body = json.loads(msg[start:end])
    assert "project_context" not in body
    assert body.get("target_feature") == "Login"


def test_generation_prompt_rule_references_project_context_block():
    msg = build_generation_user_message(
        _make_parsed(),
        existing=[],
        project_description="x",
        target_feature_name="Login",
    )
    assert "Apply the PROJECT CONTEXT block" in msg


def test_iterate_prompt_includes_project_context_block():
    msg = build_iterate_user_message(
        existing=[],
        instruction="add more edge cases",
        feature_filter=None,
        type_filter=None,
        project_description="domain-specific rules apply",
    )
    assert "=== PROJECT CONTEXT (apply to every test case) ===" in msg
    assert "domain-specific rules apply" in msg
