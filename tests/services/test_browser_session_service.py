"""Tests for backend.services.browser_session helpers."""

from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest

from backend.services import browser_session as bs


@pytest.fixture
async def db():
    """In-memory sqlite with the minimal browser_sessions schema for tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE browser_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            url TEXT NOT NULL,
            feature_name TEXT,
            browser_type TEXT,
            steps_json TEXT,
            metadata_json TEXT DEFAULT '{}',
            status TEXT,
            created_at TEXT
        );
    """)
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()
        os.unlink(path)


async def test_update_feature_name_writes_column(db):
    session = await bs.create_session(
        db, project_id="p1", user_id="u1", url="https://example.com",
        feature_name="",
    )
    await db.commit()

    updated = await bs.update_feature_name(db, session.id, "my_feature")
    await db.commit()

    assert updated is not None
    assert updated.feature_name == "my_feature"

    # Confirm persisted by re-reading.
    re_read = await bs.get_session(db, session.id)
    assert re_read.feature_name == "my_feature"


async def test_update_feature_name_returns_none_for_missing_session(db):
    result = await bs.update_feature_name(db, "bs_does_not_exist", "x")
    assert result is None
