"""FastAPI dependencies: auth and database user resolution.

``get_current_user_id`` is the main guard for protected routes. Two modes:
- **auth_disabled** (typical local dev): auto-use a single synthetic "local dev" user.
- **JWT enabled**: require ``Authorization: Bearer <token>`` from login/register.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException

from backend.config import get_settings
from backend.db import get_db
from backend.repositories import user_repo


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve authenticated user id (JWT) or local dev user when auth_disabled."""
    settings = get_settings()
    if settings.auth_disabled:
        async with get_db() as db:
            return await user_repo.ensure_local_dev_user(db)

    if not settings.jwt_secret:
        raise HTTPException(
            status_code=500,
            detail="JWT_SECRET must be set in .env when auth is enabled (or set AUTH_DISABLED=true for local dev)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:].strip()
    try:
        from backend.services.auth_service import decode_token

        return decode_token(token, settings)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None
