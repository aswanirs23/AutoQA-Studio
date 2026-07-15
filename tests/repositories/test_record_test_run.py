"""Round-trip tests for record_test_run persisting the last run's page snapshot.

The self-heal button's availability after a reload depends on last_run_page_snapshot
surviving a write -> read cycle, so these tests pin that behavior down.
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest
from backend.config import get_settings
from backend.db import get_db, init_db, reset_init_flag_for_tests
from backend.repositories import testcase_repo


@pytest.fixture(autouse=True)
async def _isolated_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let init_db() create the file fresh
    prev_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = path
    get_settings.cache_clear()
    reset_init_flag_for_tests()
    await init_db()
    async with get_db() as db:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute("INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                         ("u1", "Test User", "u1@test.local", "x", now))
        await db.execute("INSERT INTO projects (id, user_id, name, created_at) VALUES (?, ?, ?, ?)",
                         ("p1", "u1", "Proj", now))
        await db.execute("INSERT INTO features (id, project_id, name) VALUES (?, ?, ?)",
                         ("f1", "p1", "Feat"))
        await db.execute(
            "INSERT INTO test_cases (id, project_id, feature_id, title, type, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tc1", "p1", "f1", "Verify add to cart", "smoke", "high", now),
        )
        await db.commit()
    try:
        yield
    finally:
        if prev_path is not None:
            os.environ["DATABASE_PATH"] = prev_path
        else:
            os.environ.pop("DATABASE_PATH", None)
        get_settings.cache_clear()
        reset_init_flag_for_tests()
        if os.path.exists(path):
            os.unlink(path)


async def test_record_test_run_persists_page_snapshot():
    async with get_db() as db:
        ok = await testcase_repo.record_test_run(
            db, "p1", "tc1", "error", "screenshotbytes", "button: Add to cart\nheading: Products",
        )
        await db.commit()
    assert ok

    async with get_db() as db:
        tc = await testcase_repo.get_test_case(db, "p1", "tc1")
    assert tc is not None
    assert tc.last_run_status == "error"
    assert tc.last_run_screenshot_b64 == "screenshotbytes"
    assert tc.last_run_page_snapshot == "button: Add to cart\nheading: Products"


async def test_record_test_run_empty_snapshot_stored_as_none():
    # A pre-run rejection / runner crash carries no snapshot; it must not be
    # persisted as an empty string that the frontend could misread as "present".
    async with get_db() as db:
        await testcase_repo.record_test_run(db, "p1", "tc1", "error", None, "")
        await db.commit()

    async with get_db() as db:
        tc = await testcase_repo.get_test_case(db, "p1", "tc1")
    assert tc is not None
    assert tc.last_run_page_snapshot is None


async def test_record_test_run_snapshot_appears_in_list_query():
    async with get_db() as db:
        await testcase_repo.record_test_run(db, "p1", "tc1", "failed", None, "heading: Dashboard")
        await db.commit()

    async with get_db() as db:
        cases = await testcase_repo.list_test_cases_for_project(db, "p1")
    assert len(cases) == 1
    assert cases[0].last_run_page_snapshot == "heading: Dashboard"
