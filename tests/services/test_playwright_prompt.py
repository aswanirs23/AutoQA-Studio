from backend.prompts.templates import build_playwright_user_message

TC = {"title": "Verify valid login", "preconditions": "", "steps": ["Enter user", "Enter pass", "Click Login"], "expected_result": "Lands on dashboard"}


def test_login_mode_prompt_uses_credential_params():
    msg = build_playwright_user_message(TC, "http://x", is_login=True, has_credentials=True)
    assert "async def test(page, base_url, username, password)" in msg
    assert "username" in msg and "password" in msg


def test_non_login_mode_uses_landing_path():
    msg = build_playwright_user_message(
        {"title": "Nav menu", "preconditions": "", "steps": [], "expected_result": "menu shows"},
        "http://x", is_login=False, landing_path="/inventory.html")
    assert "/inventory.html" in msg
    assert "async def test(page, base_url)" in msg


def test_snapshot_injected_when_present():
    msg = build_playwright_user_message(
        TC, "http://x", is_login=False, landing_path="/inventory.html",
        page_snapshot='- button "Add to cart"\n- link "Cart"')
    assert "LIVE PAGE SNAPSHOT" in msg
    assert 'button "Add to cart"' in msg


def test_no_snapshot_section_when_absent():
    msg = build_playwright_user_message(TC, "http://x", is_login=False)
    assert "LIVE PAGE SNAPSHOT" not in msg


import pytest
from backend.config import get_effective_settings


async def test_generate_forwards_snapshot_to_prompt(monkeypatch):
    import backend.services.llm_service as svc

    captured = {}

    def fake_build(tc, base_url, **kwargs):
        captured.update(kwargs)
        return "async def test(page, base_url): pass"  # ensure validity gate passes

    # generate_playwright_code imports build_playwright_user_message locally
    # (`from backend.prompts.templates import ...` inside the function body),
    # so patching it on `svc` has no effect — the local import re-binds the
    # name every call. Patch the real reference site instead.
    import backend.prompts.templates as templates

    monkeypatch.setattr(templates, "build_playwright_user_message", fake_build)
    monkeypatch.setattr(svc, "effective_llm_provider", lambda *a, **k: "openai")
    monkeypatch.setattr(svc, "resolved_model_id", lambda *a, **k: "gpt-x")

    class _Msg:  # minimal OpenAI response shape
        def __init__(self): self.content = "async def test(page, base_url):\n    pass\n"
    class _Choice:
        def __init__(self): self.message = _Msg()
    class _Resp:
        def __init__(self): self.choices = [_Choice()]

    class _Chat:
        async def create(self, **kw): return _Resp()
    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = type("C", (), {"completions": _Chat()})()

    monkeypatch.setattr(svc, "log_openai_usage", lambda *a, **k: None)
    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeClient)

    settings = get_effective_settings()
    settings = settings.model_copy(update={"openai_api_key": "sk-test"})

    code = await svc.generate_playwright_code(
        {"title": "t", "preconditions": "", "steps": [], "expected_result": "e"},
        "http://x", settings, page_snapshot="- button \"Buy\"")
    assert "async def test" in code
    assert captured.get("page_snapshot") == '- button "Buy"'


def test_authenticated_directive_present_when_authenticated():
    msg = build_playwright_user_message(
        TC, "http://x", is_login=False, landing_path="/inventory.html", authenticated=True)
    assert "ALREADY authenticated" in msg
    assert "do NOT" in msg and "login" in msg.lower()


def test_no_authenticated_directive_by_default():
    msg = build_playwright_user_message(TC, "http://x", is_login=False, landing_path="/inventory.html")
    assert "ALREADY authenticated" not in msg
