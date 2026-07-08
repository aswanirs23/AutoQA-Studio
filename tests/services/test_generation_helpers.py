from backend.services.playwright_login import resolve_landing_path, is_login_test


def test_resolve_landing_path_precedence():
    assert resolve_landing_path({"home_path": "/dash", "success_check": "/inv"}) == "/dash"
    assert resolve_landing_path({"success_check": "/inventory.html"}) == "/inventory.html"
    assert resolve_landing_path({"success_check": "Products"}) == ""
    assert resolve_landing_path({}) == ""


def test_is_login_test_cues():
    assert is_login_test("Verify valid login grants access", []) is True
    assert is_login_test("Sign in with correct credentials", []) is True
    assert is_login_test("Check homepage", ["User clicks Log in link"]) is True
    assert is_login_test("Add item to cart", ["Click Add to cart"]) is False
    assert is_login_test("Verify navigation menu opens", []) is False
