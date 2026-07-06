"""Smoke tests for the browser_session router — checks empty goal accepted."""

from backend.routers.browser_session import ExploreStartBody


def test_explore_start_body_accepts_empty_goal():
    body = ExploreStartBody()
    assert body.goal == ""


def test_explore_start_body_accepts_explicit_goal():
    body = ExploreStartBody(goal="Test the login flow")
    assert body.goal == "Test the login flow"
