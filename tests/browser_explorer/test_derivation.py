"""Tests for goal + feature name derivation from the first snapshot."""

import pytest

from backend.services.browser_explorer.drivers import Snapshot
from backend.services.browser_explorer.orchestrator import (
    _derive_feature_name,
    _derive_goal_from_snapshot,
)


def _snap(title: str, elements: list[dict]) -> Snapshot:
    return Snapshot({
        "url": "https://example.com",
        "title": title,
        "elements": elements,
        "text_dump": "",
        "summary": "",
    })


def _btn(name: str) -> dict:
    return {"ref": "e1", "role": "button", "name": name, "disabled": False,
            "tag": "", "testid": None, "text": "", "type": None}


def _input() -> dict:
    return {"ref": "e2", "role": "textbox", "name": "Email", "disabled": False,
            "tag": "", "testid": None, "text": "", "type": None}


# ---- Goal derivation -------------------------------------------------------

def test_derive_goal_with_title_buttons_and_forms():
    snap = _snap("Dashboard Filters", [_input(), _btn("Apply"), _btn("Reset")])
    goal = _derive_goal_from_snapshot(snap)
    assert "Dashboard Filters" in goal
    assert "1 input field" in goal
    assert "2 primary action" in goal
    assert "'Apply'" in goal
    assert "'Reset'" in goal


def test_derive_goal_buttons_only():
    snap = _snap("Page", [_btn("Submit")])
    goal = _derive_goal_from_snapshot(snap)
    assert "1 primary action" in goal
    assert "input field" not in goal


def test_derive_goal_forms_only():
    snap = _snap("Form Page", [_input()])
    goal = _derive_goal_from_snapshot(snap)
    assert "1 input field" in goal
    assert "primary action" not in goal


def test_derive_goal_falls_back_to_generic_when_no_affordances():
    snap = _snap("Static Page", [])
    goal = _derive_goal_from_snapshot(snap)
    # Generic A-fallback wording from the spec
    assert "Explore this page and document" in goal


def test_derive_goal_handles_missing_title():
    snap = _snap("", [_btn("Go")])
    goal = _derive_goal_from_snapshot(snap)
    assert "On this page:" in goal
    assert "On the page titled" not in goal


def test_derive_goal_caps_button_examples_at_three():
    buttons = [_btn(f"Btn{i}") for i in range(10)]
    snap = _snap("Page", buttons)
    goal = _derive_goal_from_snapshot(snap)
    assert "10 primary action" in goal
    # Only the first 3 button names are quoted; later ones are not.
    assert "'Btn0'" in goal
    assert "'Btn1'" in goal
    assert "'Btn2'" in goal
    assert "'Btn3'" not in goal


def test_derive_goal_unicode_title():
    snap = _snap("ダッシュボード", [_btn("適用")])
    goal = _derive_goal_from_snapshot(snap)
    assert "ダッシュボード" in goal
    assert "'適用'" in goal


# ---- Feature name derivation ----------------------------------------------

def test_derive_feature_name_from_title():
    snap = _snap("My Dashboard Page", [])
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert name == "my_dashboard_page"


def test_derive_feature_name_strips_punctuation():
    snap = _snap("Sign In - Acme Co.", [])
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert name == "sign_in_acme_co"


def test_derive_feature_name_caps_at_60_chars():
    snap = _snap("a " * 100, [])  # very long title
    name = _derive_feature_name(snap, "bs_abc12345def6")
    assert len(name) <= 60


def test_derive_feature_name_falls_back_when_title_empty(sample_session_id):
    snap = _snap("", [])
    name = _derive_feature_name(snap, sample_session_id)
    # Last 8 chars of the session ID
    assert name == f"browser_session_{sample_session_id[-8:]}"


def test_derive_feature_name_falls_back_when_title_only_punct(sample_session_id):
    snap = _snap("!!!", [])
    name = _derive_feature_name(snap, sample_session_id)
    assert name.startswith("browser_session_")
