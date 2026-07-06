"""Round-trip tests for generation_repo: create + list_generations_with_outputs."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from backend.repositories.generation_repo import (
    create_generation,
    list_generations_with_outputs,
)


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY);
        CREATE TABLE features (id TEXT PRIMARY KEY, project_id TEXT);
        CREATE TABLE test_cases (
            id TEXT PRIMARY KEY, project_id TEXT, feature_id TEXT,
            title TEXT, type TEXT, preconditions TEXT, steps TEXT,
            expected_result TEXT, priority TEXT, hash TEXT,
            source_ref TEXT NOT NULL DEFAULT '', created_at TEXT
        );
        CREATE TABLE generations (
            id TEXT PRIMARY KEY, project_id TEXT, feature_id TEXT,
            trigger TEXT, source_ref TEXT, summary TEXT, created_at TEXT
        );
        CREATE TABLE generation_inputs (
            id TEXT PRIMARY KEY, generation_id TEXT,
            source_type TEXT, url TEXT, text_content TEXT, image_path TEXT,
            summary TEXT, sort_order INTEGER
        );
        INSERT INTO projects(id) VALUES ('p1');
        INSERT INTO features(id, project_id) VALUES ('f1', 'p1');
        INSERT INTO features(id, project_id) VALUES ('f2', 'p1');
    """)
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()
        os.unlink(path)


async def _insert_test_case(db, *, tc_id, feature_id, source_ref):
    await db.execute(
        "INSERT INTO test_cases(id, project_id, feature_id, title, source_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tc_id, "p1", feature_id, f"title-{tc_id}", source_ref, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def test_create_generation_round_trip(db):
    inputs = [
        {"source_type": "jira", "url": "https://x/PROJ-1", "summary": "Jira: PROJ-1", "sort_order": 0},
        {"source_type": "screenshot", "image_path": "feature/f1/abc.png", "summary": "ui.png", "sort_order": 1},
    ]
    gen = await create_generation(
        db,
        project_id="p1",
        feature_id="f1",
        trigger="generate",
        source_ref="Jira: PROJ-1 | Screenshot upload",
        summary="Jira + Screenshot",
        inputs=inputs,
    )
    await db.commit()
    assert gen.id
    assert gen.trigger == "generate"
    assert len(gen.inputs) == 2
    assert gen.inputs[0].source_type == "jira"
    assert gen.inputs[0].url == "https://x/PROJ-1"
    assert gen.inputs[1].image_path == "feature/f1/abc.png"


async def test_list_generations_returns_with_test_case_ids(db):
    gen = await create_generation(
        db,
        project_id="p1",
        feature_id="f1",
        trigger="generate",
        source_ref="Jira: PROJ-1",
        summary="Jira: PROJ-1",
        inputs=[{"source_type": "jira", "url": "https://x/PROJ-1", "summary": "PROJ-1", "sort_order": 0}],
    )
    await db.commit()

    await _insert_test_case(db, tc_id="tc1", feature_id="f1", source_ref="Jira: PROJ-1")
    await _insert_test_case(db, tc_id="tc2", feature_id="f1", source_ref="Jira: PROJ-1")
    # Different feature — should NOT match
    await _insert_test_case(db, tc_id="tc3", feature_id="f2", source_ref="Jira: PROJ-1")

    rows = await list_generations_with_outputs(db, "f1")
    assert len(rows) == 1
    g, ids = rows[0]
    assert g.id == gen.id
    assert sorted(ids) == ["tc1", "tc2"]


async def test_list_generations_hides_when_no_test_cases(db):
    await create_generation(
        db,
        project_id="p1",
        feature_id="f1",
        trigger="generate",
        source_ref="orphan-ref",
        summary="orphan",
        inputs=[{"source_type": "text", "text_content": "x", "summary": "x", "sort_order": 0}],
    )
    await db.commit()
    rows = await list_generations_with_outputs(db, "f1")
    assert rows == []


async def test_list_generations_newest_first(db):
    g_old = await create_generation(
        db, project_id="p1", feature_id="f1",
        trigger="generate", source_ref="r1", summary="r1", inputs=[],
    )
    # Force older timestamp
    await db.execute("UPDATE generations SET created_at = ? WHERE id = ?",
                     ("2020-01-01T00:00:00+00:00", g_old.id))
    g_new = await create_generation(
        db, project_id="p1", feature_id="f1",
        trigger="iterate", source_ref="r2", summary="r2", inputs=[],
    )
    await db.commit()

    await _insert_test_case(db, tc_id="t1", feature_id="f1", source_ref="r1")
    await _insert_test_case(db, tc_id="t2", feature_id="f1", source_ref="r2")
    rows = await list_generations_with_outputs(db, "f1")
    assert [g.id for g, _ in rows] == [g_new.id, g_old.id]
