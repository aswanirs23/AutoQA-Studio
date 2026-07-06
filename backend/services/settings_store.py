"""Load and persist API keys from SQLite into ``config.SECRET_OVERRIDES``.

The FastAPI lifespan calls ``load_overrides_from_db`` at startup. After ``PUT /api/settings/keys``,
``save_overrides`` merges into SQLite and reloads memory so ``get_effective_settings()``
immediately sees new values without restarting the process.
"""

from __future__ import annotations

from backend.config import SECRET_OVERRIDABLE_KEYS, SECRET_OVERRIDES
from backend.db import get_db
from backend.repositories import settings_repo


def _normalize_stored(raw: dict) -> dict[str, str]:
    """Drop unknown keys and empty strings so the DB stays a clean map of secret strings."""
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k not in SECRET_OVERRIDABLE_KEYS:
            continue
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


async def load_overrides_from_db() -> None:
    """Replace ``SECRET_OVERRIDES`` with the contents of ``app_settings.secrets_json``."""
    async with get_db() as db:
        raw = await settings_repo.get_secrets_json(db)
    merged = _normalize_stored(raw)
    SECRET_OVERRIDES.clear()
    SECRET_OVERRIDES.update(merged)


async def save_overrides(updates: dict[str, str]) -> None:
    """Merge updates into stored JSON. Only keys in SECRET_OVERRIDABLE_KEYS; empty string removes override."""
    async with get_db() as db:
        current = _normalize_stored(await settings_repo.get_secrets_json(db))
        for k, v in updates.items():
            if k not in SECRET_OVERRIDABLE_KEYS:
                continue
            if not str(v).strip():
                current.pop(k, None)
            else:
                current[k] = str(v).strip()
        await settings_repo.set_secrets_json(db, current)
    await load_overrides_from_db()
