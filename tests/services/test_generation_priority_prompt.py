from backend.models.test_case import TestCase
from backend.services.parsers.base import ParsedInput
from backend.prompts.templates import (
    SYSTEM_PROMPT, build_generation_user_message, build_iterate_user_message,
)


def _parsed():
    return ParsedInput(source_type="screenshot", feature_name="Login",
                       raw_context="Login screen with Email, Password, Sign in button")


def test_system_prompt_subordinates_cosmetics_to_workflows():
    s = SYSTEM_PROMPT.lower()
    assert "core functional workflow" in s
    assert "cosmetic" in s and "secondary" in s


def test_generation_prompt_guarantees_core_workflows_first():
    msg = build_generation_user_message(_parsed(), [], min_test_cases=10)
    low = msg.lower()
    # Core-first guarantee + mandatory-even-if-count-small
    assert "core functional workflow" in low
    assert "before any" in low and "cosmetic" in low
    # login example baked in
    assert "valid" in low and "invalid" in low
    # count interaction wording present when a count is given
    assert "fill" in low or "remainder" in low


def test_generation_prompt_small_count_still_lists_core_first():
    # With no count, the guarantee text is still present (not gated on count)
    msg = build_generation_user_message(_parsed(), [])
    assert "core functional workflow" in msg.lower()


def test_iterate_prompt_uses_core_first_framing():
    # signature: build_iterate_user_message(existing, instruction, feature_filter, type_filter, ...)
    msg = build_iterate_user_message([], "add more login tests", None, None)
    assert "core" in msg.lower() and "critical" in msg.lower()
