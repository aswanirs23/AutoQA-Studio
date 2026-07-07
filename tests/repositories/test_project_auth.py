import os
import tempfile
from datetime import datetime, timezone

import pytest
from backend.config import get_settings
from backend.db import get_db, init_db, reset_init_flag_for_tests
from backend.repositories import project_repo


@pytest.fixture(autouse=True)
async def _isolated_db():
    """Point DATABASE_PATH at a throwaway temp file for this test module.

    ``project_repo`` functions go through ``backend.db.get_db``, which by default
    resolves to the real dev DB (``data/testgen.db``) per ``.env``. Without this
    fixture these tests would read/write that file. It also seeds a ``u1`` user
    row so the ``projects.user_id`` foreign key is satisfiable, since the test
    bodies (from the task brief) reference "u1" without creating it.
    """
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
        await db.execute(
            "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("u1", "Test User", "u1@test.local", "x", now),
        )
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


async def _mk_project(db):
    p = await project_repo.create_project(db, "u1", "Proj", "desc")
    return p.id


async def test_update_and_get_auth_config_round_trip():
    await init_db()
    async with get_db() as db:
        pid = await _mk_project(db)
        ok = await project_repo.update_project_auth(
            db, "u1", pid,
            {"login_url": "http://x/login", "username": "u", "password": "p",
             "selectors": {}, "success_check": "/home", "verified_at": ""},
        )
        assert ok is True
        auth = await project_repo.get_project_auth(db, "u1", pid)
        assert auth["username"] == "u"
        assert auth["password"] == "p"
        # And it is exposed on the Project model (raw at repo layer)
        proj = await project_repo.get_project(db, "u1", pid)
        assert proj.auth_config["login_url"] == "http://x/login"


async def test_get_auth_for_missing_project_returns_none():
    await init_db()
    async with get_db() as db:
        assert await project_repo.get_project_auth(db, "u1", "nope") is None
