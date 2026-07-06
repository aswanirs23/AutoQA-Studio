"""Pydantic model tests for browser_session API bodies."""

from backend.models.browser_session import StartSessionBody


def test_start_session_body_accepts_missing_feature_name():
    body = StartSessionBody(project_id="p1", url="https://example.com")
    assert body.feature_name == ""


def test_start_session_body_accepts_explicit_feature_name():
    body = StartSessionBody(
        project_id="p1", url="https://example.com", feature_name="Login",
    )
    assert body.feature_name == "Login"
