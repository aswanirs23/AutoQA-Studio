"""SQLite database: schema init and connection helpers.

Uses aiosqlite; swap this module + repositories for Postgres later.
Database file path is resolved under the project root (parent of ``backend/``).

**Tables (see SCHEMA below):** ``users``, ``projects``, ``features``, ``test_cases``,
``input_history`` (audit of generation runs), ``app_settings`` (JSON blob for UI API keys).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from backend.config import get_settings

_lock = asyncio.Lock()
_initialized = False

# Full DDL run on first connection per process; IF NOT EXISTS keeps it idempotent.
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS test_cases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    feature_id TEXT NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'happy',
    preconditions TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium',
    hash TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tc_project ON test_cases(project_id);
CREATE INDEX IF NOT EXISTS idx_tc_feature ON test_cases(feature_id);
CREATE INDEX IF NOT EXISTS idx_tc_hash ON test_cases(project_id, hash);

CREATE TABLE IF NOT EXISTS input_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    feature_id TEXT REFERENCES features(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_input_project ON input_history(project_id);

CREATE TABLE IF NOT EXISTS app_settings (
    id TEXT PRIMARY KEY,
    secrets_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    feature_name TEXT NOT NULL DEFAULT '',
    browser_type TEXT NOT NULL DEFAULT 'playwright',
    steps_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'recording',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bs_project ON browser_sessions(project_id);

CREATE TABLE IF NOT EXISTS figma_cache (
    cache_key TEXT PRIMARY KEY,
    file_key TEXT NOT NULL,
    node_id TEXT NOT NULL DEFAULT '',
    max_vision_frames INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    cached_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_figma_cache_file ON figma_cache(file_key);

CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    feature_id TEXT NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_generations_feature ON generations(feature_id);
CREATE INDEX IF NOT EXISTS idx_generations_project ON generations(project_id);

CREATE TABLE IF NOT EXISTS generation_inputs (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    url TEXT,
    text_content TEXT,
    image_path TEXT,
    summary TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_generation_inputs_gen ON generation_inputs(generation_id);
"""


def database_file_path() -> Path:
    """Absolute path to SQLite file."""
    settings = get_settings()
    raw = (settings.database_path or "data/testgen.db").strip()
    p = Path(raw)
    if p.is_absolute():
        return p
    root = Path(__file__).resolve().parent.parent
    return root / p


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
    """Add column if missing (SQLite migration)."""
    rows = await fetch_all(db, f"PRAGMA table_info({table})")
    names = {r["name"] for r in rows}
    if column not in names:
        await db.execute(ddl)


async def migrate_schema(db: aiosqlite.Connection) -> None:
    """Apply additive migrations for existing databases."""
    await _ensure_column(
        db,
        "test_cases",
        "source_ref",
        "ALTER TABLE test_cases ADD COLUMN source_ref TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "projects",
        "description",
        "ALTER TABLE projects ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "browser_sessions",
        "metadata_json",
        "ALTER TABLE browser_sessions ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
    )
    await _ensure_column(
        db,
        "projects",
        "base_url",
        "ALTER TABLE projects ADD COLUMN base_url TEXT NOT NULL DEFAULT ''",
    )
    await _ensure_column(
        db,
        "test_cases",
        "last_run_status",
        "ALTER TABLE test_cases ADD COLUMN last_run_status TEXT",
    )
    await _ensure_column(
        db,
        "test_cases",
        "last_run_at",
        "ALTER TABLE test_cases ADD COLUMN last_run_at TEXT",
    )
    await _ensure_column(
        db,
        "test_cases",
        "last_run_screenshot_b64",
        "ALTER TABLE test_cases ADD COLUMN last_run_screenshot_b64 TEXT",
    )


async def init_db() -> None:
    """Create tables once per process (idempotent)."""
    global _initialized
    async with _lock:
        if _initialized:
            return
        path = database_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(SCHEMA)
            await migrate_schema(db)
            await db.commit()
        _initialized = True


def reset_init_flag_for_tests() -> None:
    global _initialized
    _initialized = False


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager: one connection per unit of work; commits on success."""
    await init_db()
    path = database_file_path()
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def fetch_one(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    """Return one row as ``aiosqlite.Row`` (dict-like by column name) or None."""
    async with db.execute(sql, params) as cur:
        row = await cur.fetchone()
        return row


async def fetch_all(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    """Return all rows as a list of ``aiosqlite.Row`` objects."""
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
        return list(rows)
