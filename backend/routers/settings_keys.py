"""Read and update API keys stored in SQLite.

GET returns asterisk-masked values and flags only. PUT accepts a partial dict: present
keys are updated; empty string removes the stored value for that key.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from backend.config import SECRET_OVERRIDABLE_KEYS, SECRET_OVERRIDES, get_effective_settings
from backend.db import fetch_one, get_db
from backend.deps import get_current_user_id
from backend.services.settings_store import save_overrides

router = APIRouter(prefix="/settings", tags=["settings"])


def _mask(val: str) -> str:
    """Asterisk-masked form; last 4 characters shown when length allows confirmation."""
    v = (val or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return "*" * 8
    stars = "*" * min(12, max(8, len(v) - 4))
    return stars + v[-4:]


@router.get("/keys")
async def get_api_keys_status(_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Return per-key status (masked); never returns full secrets."""
    merged = get_effective_settings()
    keys: dict[str, dict[str, Any]] = {}
    for name in sorted(SECRET_OVERRIDABLE_KEYS):
        eff = str(getattr(merged, name, "") or "")
        from_ui = name in SECRET_OVERRIDES
        keys[name] = {
            "configured": bool(eff.strip()),
            "from_ui": from_ui,
            "from_env_only": False,
            "masked": _mask(eff),
        }
    return {"keys": keys}


@router.put("/keys")
async def put_api_keys(
    body: dict[str, str],
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Set or clear UI-stored keys. Only keys present in the body are updated; empty string clears the UI override."""
    unknown = set(body.keys()) - SECRET_OVERRIDABLE_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown keys: {sorted(unknown)}")
    await save_overrides(body)
    return {"status": "ok"}


@router.post("/test-figma")
async def test_figma_token(
    body: dict[str, str] | None = None,
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Validate a Figma token by hitting /v1/me. Uses provided token if given, else the stored one."""
    token = ((body or {}).get("token") or "").strip()
    if not token:
        token = (get_effective_settings().figma_access_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="No Figma token to test — paste one or save it first.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://api.figma.com/v1/me", headers={"X-Figma-Token": token})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Figma: {e}") from e

    if r.status_code == 403:
        raise HTTPException(status_code=400, detail="Figma rejected the token (403 — missing 'File content' scope?).")
    if r.status_code in (401, 404):
        raise HTTPException(status_code=400, detail="Figma rejected the token (invalid or expired).")
    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Figma returned HTTP {r.status_code}.")

    try:
        data = r.json()
    except ValueError:
        data = {}
    return {
        "ok": True,
        "email": data.get("email") or "",
        "handle": data.get("handle") or "",
    }


@router.get("/figma-cache")
async def figma_cache_status(_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Return how many Figma fetches are currently cached."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT COUNT(*) AS n FROM figma_cache")
    return {"count": int(row["n"]) if row else 0}


@router.delete("/figma-cache")
async def clear_figma_cache(
    body: dict[str, str] | None = None,
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Clear Figma cache. Optional body: {"file_key": "..."} to scope to one file."""
    file_key = ((body or {}).get("file_key") or "").strip()
    async with get_db() as db:
        if file_key:
            await db.execute("DELETE FROM figma_cache WHERE file_key = ?", (file_key,))
        else:
            await db.execute("DELETE FROM figma_cache")
    return {"ok": True, "cleared_file_key": file_key or None}
