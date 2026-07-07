from backend.services.playwright_login import (
    mask_auth_config, auth_storage_path, looks_like_login_page, build_login_script,
)


def test_mask_hides_password():
    masked = mask_auth_config({"login_url": "u", "username": "bob", "password": "s3cret"})
    assert "password" not in masked
    assert masked["password_set"] is True
    assert masked["username"] == "bob"
    assert mask_auth_config({"username": "x"})["password_set"] is False


def test_storage_path_uses_project_id():
    p = auth_storage_path("abc-123")
    assert p.name == "abc-123.json"
    assert p.parent.name == "auth"


def test_login_page_detection():
    assert looks_like_login_page("http://x/login", "Sign in", "http://x/login") is True
    assert looks_like_login_page("http://x/dashboard", "Welcome", "http://x/login") is False
    # password field present in text-ish signal
    assert looks_like_login_page("http://x/", "please Log in to continue", "http://x/login") is True


def test_build_login_script_embeds_values_and_is_runnable_source():
    src = build_login_script(
        {"login_url": "http://x/login", "username": "u", "password": "p",
         "selectors": {}, "success_check": "/home"},
        base_url="http://x", storage_path="/tmp/s.json", headless=True,
    )
    assert "u" in src and "http://x/login" in src and "/tmp/s.json" in src
    compile(src, "<login>", "exec")  # must be valid Python
