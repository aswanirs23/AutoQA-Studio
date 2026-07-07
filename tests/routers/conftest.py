"""Fixtures for router integration tests.

Each test gets a clean DB by pointing DATABASE_PATH at a temp file before the app
is imported. The app is instantiated per-session; the local dev user (used when
AUTH_DISABLED=true) is created lazily on the first request.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _routers_db_env() -> Iterator[None]:
    """Point the app at a per-run DB BEFORE backend.main imports.

    Clears backend.config.get_settings's lru_cache so the new DATABASE_PATH
    is picked up even if another test triggered Settings instantiation first.
    """
    import shutil

    from backend.config import get_settings

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="testcase_routers_"))
    db_path = tmp / "routers.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    os.environ["AUTH_DISABLED"] = "true"
    get_settings.cache_clear()

    try:
        yield
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def client() -> Iterator["TestClient"]:  # noqa: F821
    """Sync TestClient bound to the FastAPI app, imported lazily so env vars above take effect.

    Entering the TestClient as a context manager runs the app's lifespan (init_db +
    load_overrides_from_db), matching what a real server startup would do.
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as tc:
        yield tc
