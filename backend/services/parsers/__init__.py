"""Import parser modules to register plugins with ParserRegistry.

Each imported submodule calls ParserRegistry.register(...) at load time.
Registration order here = tab order in the web UI (put the default parser first).
"""
# Registration order = tab order in UI (text first for MVP).
from backend.services.parsers import text_parser  # noqa: F401
from backend.services.parsers import figma_parser  # noqa: F401
from backend.services.parsers import jira_parser  # noqa: F401
from backend.services.parsers import screenshot_parser  # noqa: F401
from backend.services.parsers import browser_session_parser  # noqa: F401
from backend.services.parsers.base import BaseParser, ParsedInput, ParserMeta
from backend.services.parsers.registry import ParserRegistry

__all__ = [
    "BaseParser",
    "ParsedInput",
    "ParserMeta",
    "ParserRegistry",
]
