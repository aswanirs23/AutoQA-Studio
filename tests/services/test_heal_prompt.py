# tests/services/test_heal_prompt.py
from backend.prompts.templates import build_heal_prompt

TC = {"title": "Verify nav menu opens", "steps": ["Click the menu icon"], "expected_result": "menu shows"}


def test_heal_prompt_includes_snapshot_code_and_directive():
    msg = build_heal_prompt(TC, "async def test(page, base_url):\n    assert False\n",
                            "button: Open Menu\nnav: Main", "AssertionError: nope")
    assert "button: Open Menu" in msg          # the observed snapshot
    assert "async def test(page, base_url):" in msg  # the current code
    assert "observed" in msg.lower()           # 'the observed page is correct' directive
    assert "suggested_expected" in msg and "suggested_code" in msg  # JSON contract
