# Input parsers

Parsers are the extension point for input sources. Each one turns some input into the common `ParsedInput` shape that the LLM layer consumes. The set of parsers is exposed at `GET /api/parsers`, which also drives the dynamic UI form for each tab.

## Built-in parsers

| `input_type` | Module | Purpose | Extra configuration |
|--------------|--------|---------|---------------------|
| `text` | `text_parser.py` | Paste requirements / free text | None (LLM keys only) |
| `figma` | `figma_parser.py` | Figma file or frame URL → structure + text | Figma access token |
| `jira` | `jira_parser.py` | Fetch issue by key or link (REST API) | Jira base URL, email, API token. Optional `include_linked` |
| `screenshot` | `screenshot_parser.py` | Image upload → vision summary → tests | Multipart `file`; uses provider's vision API |
| `browser_session` | `browser_session_parser.py` | Record a browser session → tests | Playwright or IDE Browser MCP |

## Adding a new input parser (plugin)

1. Create `backend/services/parsers/your_parser.py`.
2. Subclass `BaseParser`, set `meta`, implement `async def parse(self, data, file) -> ParsedInput`.
3. `ParserRegistry.register(YourParser())`.
4. Import the module in `backend/services/parsers/__init__.py` (import order there determines tab order in the UI).
