"""FastAPI application: project-based persistent test case generation.

Entry point: mounts REST routers under ``/api``, serves the static ``frontend/`` folder
at ``/`` when present (so one process can host UI + API). Startup loads SQLite schema and
in-memory API-key overrides from the database (see ``settings_store``).
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import backend.services.parsers  # noqa: F401 — register parsers
from backend.config import get_settings
from backend.db import init_db
from backend.routers import auth, browser_session, export, extract, features, generate, parsers_meta, playwright_exec, projects, settings_keys
from backend.services.settings_store import load_overrides_from_db


_settings = get_settings()
logging.basicConfig(
    level=getattr(logging, (_settings.log_level or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("testcase_agent")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run once at process start: ensure DB tables exist, then hydrate UI-stored secrets."""
    await init_db()
    await load_overrides_from_db()
    yield


app = FastAPI(
    title="AutoQA Studio",
    description="Project-based persistent test generation with pluggable input parsers.",
    version="0.2.0",
    lifespan=lifespan,
)

_origins_raw = (_settings.cors_origins or "*").strip()
_origins = ["*"] if _origins_raw == "*" else [o.strip() for o in _origins_raw.split(",") if o.strip()]
_allow_credentials = _origins != ["*"]  # CORS spec: credentials cannot combine with "*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(features.router, prefix="/api")
app.include_router(features.generations_router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(parsers_meta.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(settings_keys.router, prefix="/api")
app.include_router(extract.router, prefix="/api")
app.include_router(browser_session.router, prefix="/api")
app.include_router(playwright_exec.router, prefix="/api")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Optional: ship the SPA as static files when ``frontend/`` exists next to ``backend/``.
_frontend = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
