"""Password hashing and JWT access tokens (HS256).

Uses ``bcrypt`` for passwords and ``python-jose`` for JWT. ``decode_token`` returns the user id
stored in the ``sub`` claim; routes combine this with ``user_repo`` for authorization.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from backend.config import Settings, get_settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("ascii"))
    except Exception:
        return False


def create_access_token(user_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    secret = settings.jwt_secret
    if not secret:
        raise ValueError("jwt_secret is not configured")
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secret = settings.jwt_secret
    if not secret:
        raise ValueError("jwt_secret is not configured")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        uid = payload.get("sub")
        if not uid or not isinstance(uid, str):
            raise JWTError("missing sub")
        return uid
    except JWTError as e:
        raise ValueError(str(e)) from e
