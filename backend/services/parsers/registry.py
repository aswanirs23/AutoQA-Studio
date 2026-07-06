"""Central registry for parser plugins.

- register: called once per parser at import time.
- get: used by POST /api/generate to resolve input_type string.
- list_meta_dicts: used by GET /api/parsers for the frontend.

This is the extension point: new parsers do not require router changes.
"""

from __future__ import annotations

from typing import Any

from backend.services.parsers.base import BaseParser


class ParserRegistry:
    _parsers: dict[str, BaseParser] = {}

    @classmethod
    def register(cls, parser: BaseParser) -> None:
        name = parser.meta.name
        if name in cls._parsers:
            raise ValueError(f"Parser already registered: {name}")
        cls._parsers[name] = parser

    @classmethod
    def get(cls, name: str) -> BaseParser:
        if name not in cls._parsers:
            raise KeyError(f"Unknown input_type: {name}")
        return cls._parsers[name]

    @classmethod
    def list_meta_dicts(cls) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in cls._parsers.values():
            m = p.meta
            out.append(
                {
                    "name": m.name,
                    "display_name": m.display_name,
                    "description": m.description,
                    "input_fields": [f.model_dump() for f in m.input_fields],
                    "accepts_file": m.accepts_file,
                }
            )
        return out
