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
