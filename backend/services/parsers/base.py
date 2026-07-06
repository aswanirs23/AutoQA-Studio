"""Parser plugin interface and shared types.

How plugins work:
1. Define ParserMeta (name, display_name, form fields, accepts_file).
2. Subclass BaseParser and implement async parse(data, file) -> ParsedInput.
3. At end of module: ParserRegistry.register(MyParser()).
4. Import the module from parsers/__init__.py so registration runs at startup.

ParsedInput is the only shape the LLM layer sees — keep it stable when adding sources.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile


class InputFieldDef(BaseModel):
    name: str
    type: str = "text"  # text, url, textarea, number
    label: str = ""
    placeholder: str = ""
    required: bool = True


class ParsedInput(BaseModel):
    """Standardized output from any parser — LLM consumes this."""

    source_type: str
    feature_name: str = ""
    screens: list[str] = Field(default_factory=list)
    ui_elements: list[str] = Field(default_factory=list)
    user_actions: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    raw_context: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParserMeta(BaseModel):
    """Static metadata exposed via GET /api/parsers for dynamic forms."""

    name: str
    display_name: str
    description: str = ""
    input_fields: list[InputFieldDef] = Field(default_factory=list)
    accepts_file: bool = False


class BaseParser(ABC):
    """Subclass and set `meta`, implement `parse`."""

    meta: ParserMeta

    @abstractmethod
    async def parse(
        self,
        data: dict[str, Any],
        file: UploadFile | None,
    ) -> ParsedInput:
        """Parse user payload into ParsedInput."""


def strip_internal_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Drop keys starting with `_` from a metadata dict.

    Used so parser sidechannel data (e.g. raw image bytes under `_image_bytes`) is excluded
    from anything that gets logged or serialized to JSON (input_history rows, prompt body).
    """
    if not meta:
        return {}
    return {k: v for k, v in meta.items() if not str(k).startswith("_")}
