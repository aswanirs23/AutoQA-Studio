"""Parser metadata for dynamic frontend.

GET /api/parsers returns each plugin's name, labels, and input_fields.
The SPA uses this to render tabs and forms without hardcoding parser names.
"""

from fastapi import APIRouter

from backend.services.parsers.registry import ParserRegistry

router = APIRouter(tags=["parsers"])


@router.get("/parsers")
async def list_parsers() -> dict:
    return {"parsers": ParserRegistry.list_meta_dicts()}
