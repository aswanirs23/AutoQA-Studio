"""Manual text input parser plugin.

Steps in parse():
1. Read feature_name and content from the form JSON.
2. Build raw_context for the LLM (optional "Feature:" header).
3. Return ParsedInput with empty structured lists (text is all in raw_context).
"""

from typing import Any

from starlette.datastructures import UploadFile

from backend.services.parsers.base import BaseParser, InputFieldDef, ParsedInput, ParserMeta
from backend.services.parsers.registry import ParserRegistry


class TextParser(BaseParser):
    meta = ParserMeta(
        name="text",
        display_name="Manual text",
        description="Paste requirements, user stories, or free-form feature description.",
        input_fields=[
            InputFieldDef(
                name="feature_name",
                type="text",
                label="Feature name",
                placeholder="e.g. login",
                required=True,
            ),
            InputFieldDef(
                name="content",
                type="textarea",
                label="Requirements / description",
                placeholder="Describe the feature, flows, and validations...",
                required=True,
            ),
        ],
        accepts_file=False,
    )

    async def parse(self, data: dict[str, Any], file: UploadFile | None) -> ParsedInput:
        feature = str(data.get("feature_name") or data.get("feature") or "").strip()
        content = str(data.get("content") or data.get("text") or "").strip()
        raw = content
        if feature:
            raw = f"Feature: {feature}\n\n{content}"
        return ParsedInput(
            source_type="text",
            feature_name=feature or "general",
            screens=[],
            ui_elements=[],
            user_actions=[],
            business_rules=[],
            raw_context=raw,
            metadata={"feature_name": feature},
        )


# Self-register on import (see parsers/__init__.py import order)
ParserRegistry.register(TextParser())
