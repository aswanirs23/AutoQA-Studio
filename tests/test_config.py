"""Settings sanity checks for new browser MCP fields."""

from backend.config import Settings


def test_browser_mcp_defaults():
    s = Settings()
    assert s.browser_mcp_command == "npx"
    assert s.browser_mcp_args == "-y @browsermcp/mcp@latest"
    assert s.browser_mcp_startup_timeout_seconds == 30
    assert s.browser_mcp_tool_timeout_seconds == 30


def test_browser_mcp_args_split_on_whitespace():
    """Settings stores args as a string; consumers split on whitespace."""
    s = Settings(browser_mcp_args="--foo bar  --baz")
    assert s.browser_mcp_args.split() == ["--foo", "bar", "--baz"]
