"""Fixtures for audit smoke tests.

Each test gets a clean DB by pointing DATABASE_PATH at a temp file before the app
is imported. The app is instantiated per-session; the local dev user (used when
AUTH_DISABLED=true) is created lazily on the first request.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session", autouse=True)
def _audit_db_env() -> Iterator[None]:
    """Point the app at a per-run audit DB BEFORE backend.main imports.

    Clears backend.config.get_settings's lru_cache so the new DATABASE_PATH
    is picked up even if another test triggered Settings instantiation first.
    """
    import shutil

    from backend.config import get_settings

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="testcase_audit_"))
    db_path = tmp / "audit.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    os.environ["AUTH_DISABLED"] = "true"
    os.environ.setdefault("AUDIT_RUN_LLM", "0")
    get_settings.cache_clear()

    try:
        yield
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the FastAPI app (no real server)."""
    from backend.main import app  # imported lazily so env vars above take effect
    from backend.db import init_db
    from backend.services.settings_store import load_overrides_from_db

    # Lifespan isn't run by ASGITransport; replicate the parts we need.
    await init_db()
    await load_overrides_from_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
