"""User CRUD and the special ``ensure_local_dev_user`` row used when auth is disabled."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite

from backend.db import fetch_one
from backend.models.test_case import User


async def create_user(
    db: aiosqlite.Connection,
    name: str,
    email: str,
    password_hash: str,
) -> User:
    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, name, email.strip().lower(), password_hash, now),
    )
    return User(id=uid, name=name, email=email.strip().lower(), created_at=datetime.fromisoformat(now.replace("Z", "+00:00")))


async def get_by_email(db: aiosqlite.Connection, email: str) -> tuple[User, str] | None:
    """Return (User, password_hash) or None."""
    row = await fetch_one(
        db,
        "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
        (email.strip().lower(),),
    )
    if not row:
        return None
    u = User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
    )
    return u, row["password_hash"]


async def get_by_id(db: aiosqlite.Connection, user_id: str) -> User | None:
    row = await fetch_one(db, "SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,))
    if not row:
        return None
    return User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
    )


async def ensure_local_dev_user(db: aiosqlite.Connection) -> str:
    """Create fixed local user if missing (auth_disabled dev mode). Returns user id."""
    row = await fetch_one(db, "SELECT id FROM users WHERE email = ?", ("local@localhost",))
    if row:
        return row["id"]
    uid = "00000000-0000-4000-8000-000000000001"
    now = datetime.now(timezone.utc).isoformat()
    # dummy hash — login not used for this user
    await db.execute(
        "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, "Local Dev", "local@localhost", "-", now),
    )
    return uid
