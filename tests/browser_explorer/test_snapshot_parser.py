"""Tests for the browsermcp.io snapshot text parser."""

import pytest

from backend.services.browser_explorer.drivers.mcp_driver import (
    _parse_browsermcp_snapshot,
)


SIMPLE_PAGE = """- Page URL: https://example.com/dashboard
- Page Title: Dashboard
- generic [ref=e1]:
  - heading "Filters" [ref=e2]
  - textbox "Search" [ref=e3]
  - button "Apply" [ref=e4]
  - button "Reset" [ref=e5] [disabled]"""


EMPTY_PAGE = """- Page URL: https://example.com/blank
- Page Title: """


NO_REFS = """- Page URL: https://example.com/static
- Page Title: Static
- generic:
  - paragraph "Just text, no interactive elements."
"""


WEIRD_REFS = """- Page URL: https://example.com/x
- Page Title: X
- button "Save" [ref=e123abc]
- link "Help" [ref=e0]"""


def test_parse_simple_page_extracts_url_and_title():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    assert snap["url"] == "https://example.com/dashboard"
    assert snap["title"] == "Dashboard"


def test_parse_simple_page_extracts_elements_with_refs():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    refs = [el["ref"] for el in snap["elements"]]
    # The "generic" container has a ref but no role we care about — we still
    # include it; the orchestrator filters by role later.
    assert "e2" in refs
    assert "e3" in refs
    assert "e4" in refs
    assert "e5" in refs


def test_parse_role_and_name():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    apply_btn = next(el for el in snap["elements"] if el["ref"] == "e4")
    assert apply_btn["role"] == "button"
    assert apply_btn["name"] == "Apply"


def test_parse_disabled_flag():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    reset_btn = next(el for el in snap["elements"] if el["ref"] == "e5")
    assert reset_btn["disabled"] is True

    apply_btn = next(el for el in snap["elements"] if el["ref"] == "e4")
    assert apply_btn["disabled"] is False


def test_parse_missing_fields_have_safe_defaults():
    """Per spec: tag, testid, text, type are unavailable from browsermcp.
    Snapshot shape consumers expect these keys to be present though."""
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    el = snap["elements"][0]
    assert el["tag"] == ""
    assert el["testid"] is None
    assert el["text"] == ""
    assert el["type"] is None


def test_parse_empty_page():
    snap = _parse_browsermcp_snapshot(EMPTY_PAGE)
    assert snap["url"] == "https://example.com/blank"
    assert snap["title"] == ""
    assert snap["elements"] == []


def test_parse_page_with_no_interactive_elements():
    snap = _parse_browsermcp_snapshot(NO_REFS)
    assert snap["elements"] == []


def test_parse_weird_ref_ids():
    snap = _parse_browsermcp_snapshot(WEIRD_REFS)
    refs = [el["ref"] for el in snap["elements"]]
    assert "e123abc" in refs
    assert "e0" in refs


def test_parse_includes_text_dump_and_summary():
    snap = _parse_browsermcp_snapshot(SIMPLE_PAGE)
    assert "text_dump" in snap and isinstance(snap["text_dump"], str)
    assert "summary" in snap and isinstance(snap["summary"], str)
    # text_dump should mention each interactive element so state-hash dedup works
    assert "Apply" in snap["text_dump"]


def test_parse_malformed_input_does_not_raise():
    """If browsermcp returns garbage, return an empty-but-valid snapshot
    rather than crashing the explore loop."""
    snap = _parse_browsermcp_snapshot("garbage with no recognizable structure")
    assert snap["url"] == ""
    assert snap["title"] == ""
    assert snap["elements"] == []
