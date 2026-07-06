"""Persist API keys and integration secrets in SQLite — merged in ``get_effective_settings()``.

One row ``id='default'`` holds a JSON object of field name → string value. Empty values are
not stored; clearing a key in the UI removes it from JSON (secrets are not read from ``.env``).
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from backend.db import fetch_one

SETTINGS_ROW_ID = "default"


async def ensure_settings_row(db: aiosqlite.Connection) -> None:
    """Insert the singleton settings row if this is a fresh database."""
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (id, secrets_json) VALUES (?, ?)",
        (SETTINGS_ROW_ID, "{}"),
    )


async def get_secrets_json(db: aiosqlite.Connection) -> dict[str, Any]:
    await ensure_settings_row(db)
    row = await fetch_one(db, "SELECT secrets_json FROM app_settings WHERE id = ?", (SETTINGS_ROW_ID,))
    if not row or not row["secrets_json"]:
        return {}
    try:
        data = json.loads(row["secrets_json"])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def set_secrets_json(db: aiosqlite.Connection, secrets: dict[str, str]) -> None:
    """Persist the full secrets map (caller merges partial updates)."""
    await ensure_settings_row(db)
    await db.execute(
        "UPDATE app_settings SET secrets_json = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(secrets), SETTINGS_ROW_ID),
    )
