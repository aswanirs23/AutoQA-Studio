"""Tests for source_ref helpers — short labels for the Generations panel."""

from __future__ import annotations

from backend.services.parsers.base import ParsedInput
from backend.services.source_ref import derive_generation_summary


def _p(source_type: str, metadata: dict | None = None, feature_name: str = "", raw: str = "") -> ParsedInput:
    return ParsedInput(
        source_type=source_type,
        feature_name=feature_name,
        raw_context=raw,
        metadata=metadata or {},
    )


def test_jira_summary_uses_issue_key():
    assert derive_generation_summary(_p("jira", {"issue_key": "PROJ-123"})) == "Jira: PROJ-123"


def test_jira_summary_falls_back_to_generic():
    assert derive_generation_summary(_p("jira", {})) == "Jira"


def test_figma_summary_uses_file_name_when_present():
    assert derive_generation_summary(_p("figma", {"file_name": "Login mockup v3"})) == "Figma — Login mockup v3"


def test_figma_summary_falls_back_to_generic():
    assert derive_generation_summary(_p("figma", {})) == "Figma"


def test_text_summary_uses_feature_name():
    assert derive_generation_summary(_p("text", feature_name="Login")) == "Manual text (Login)"


def test_text_summary_generic_when_no_feature_name():
    assert derive_generation_summary(_p("text")) == "Manual text"


def test_screenshot_summary_uses_filename():
    assert derive_generation_summary(_p("screenshot", {"filename": "login-error.png"})) == "Screenshot — login-error.png"


def test_browser_session_summary_uses_goal():
    assert derive_generation_summary(
        _p("browser_session", {"goal": "test login flow", "url": "https://x.com"})
    ).startswith("Browser session")


def test_multi_summary_describes_counts():
    # 1 figma + 2 screenshots
    parsed = _p(
        "multi",
        metadata={
            "sources": [
                {"source_type": "figma", "metadata": {}},
                {"source_type": "screenshot", "metadata": {"filename": "a.png"}},
                {"source_type": "screenshot", "metadata": {"filename": "b.png"}},
            ]
        },
    )
    s = derive_generation_summary(parsed)
    assert "Figma" in s and "Screenshot" in s
    assert "2" in s  # mentions the count for screenshots


def test_multi_summary_single_of_each_lists_types():
    parsed = _p(
        "multi",
        metadata={
            "sources": [
                {"source_type": "jira", "metadata": {"issue_key": "P-1"}},
                {"source_type": "figma", "metadata": {}},
            ]
        },
    )
    s = derive_generation_summary(parsed)
    assert "Jira" in s and "Figma" in s


def test_unknown_returns_source_type():
    assert derive_generation_summary(_p("weird")) == "weird"
