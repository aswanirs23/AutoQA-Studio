"""Browser session CRUD: create, read, add steps, complete.

Sessions are stored in the ``browser_sessions`` table with step data serialised
as JSON. The Cursor agent (or any external automation) records steps via the API,
then the ``browser_session`` parser reads the completed session for LLM generation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from backend.db import fetch_all, fetch_one
from backend.models.browser_session import BrowserSession, SessionStep


def _new_id() -> str:
    return f"bs_{uuid.uuid4().hex[:12]}"


def _session_from_row(row: aiosqlite.Row) -> BrowserSession:
    steps_raw = json.loads(row["steps_json"] or "[]")
    steps = [SessionStep(**s) for s in steps_raw]
    # metadata_json is added by an additive migration; older rows may not
    # contain it depending on the DB driver version. Defensive read.
    try:
        metadata_raw = row["metadata_json"]
    except (IndexError, KeyError):
        metadata_raw = "{}"
    metadata = json.loads(metadata_raw or "{}")
    return BrowserSession(
        id=row["id"],
        project_id=row["project_id"],
        user_id=row["user_id"],
        url=row["url"],
        feature_name=row["feature_name"] or "",
        browser_type=row["browser_type"] or "playwright",
        steps=steps,
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        metadata=metadata,
    )


async def create_session(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    user_id: str,
    url: str,
    feature_name: str = "",
    browser_type: str = "playwright",
    initial_steps: list[str] | None = None,
) -> BrowserSession:
    """Create a new browser session, optionally with pending step instructions."""
    sid = _new_id()
    now = datetime.now(timezone.utc).isoformat()
    steps: list[SessionStep] = []
    if initial_steps:
        steps = [
            SessionStep(index=i, instruction=instr, status="pending")
            for i, instr in enumerate(initial_steps)
        ]
    steps_json = json.dumps([s.model_dump() for s in steps], ensure_ascii=False)

    await db.execute(
        """
        INSERT INTO browser_sessions (id, project_id, user_id, url, feature_name,
                                      browser_type, steps_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'recording', ?)
        """,
        (sid, project_id, user_id, url, feature_name, browser_type, steps_json, now),
    )
    return BrowserSession(
        id=sid,
        project_id=project_id,
        user_id=user_id,
        url=url,
        feature_name=feature_name,
        browser_type=browser_type,
        steps=steps,
        status="recording",
        created_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
    )


async def get_session(db: aiosqlite.Connection, session_id: str) -> BrowserSession | None:
    row = await fetch_one(
        db,
        "SELECT * FROM browser_sessions WHERE id = ?",
        (session_id,),
    )
    return _session_from_row(row) if row else None


async def list_sessions(
    db: aiosqlite.Connection,
    project_id: str,
    limit: int = 50,
) -> list[BrowserSession]:
    rows = await fetch_all(
        db,
        "SELECT * FROM browser_sessions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    )
    return [_session_from_row(r) for r in rows]


async def add_step(
    db: aiosqlite.Connection,
    session_id: str,
    step: SessionStep,
) -> BrowserSession | None:
    """Append a recorded step to an existing session."""
    session = await get_session(db, session_id)
    if not session:
        return None
    step.index = len(session.steps)
    session.steps.append(step)
    steps_json = json.dumps([s.model_dump() for s in session.steps], ensure_ascii=False)
    await db.execute(
        "UPDATE browser_sessions SET steps_json = ? WHERE id = ?",
        (steps_json, session_id),
    )
    return session


async def update_step(
    db: aiosqlite.Connection,
    session_id: str,
    step_index: int,
    updates: dict,
) -> BrowserSession | None:
    """Update fields on an existing step by index."""
    session = await get_session(db, session_id)
    if not session or step_index < 0 or step_index >= len(session.steps):
        return None
    existing = session.steps[step_index]
    merged = existing.model_copy(update={k: v for k, v in updates.items() if v is not None})
    session.steps[step_index] = merged
    steps_json = json.dumps([s.model_dump() for s in session.steps], ensure_ascii=False)
    await db.execute(
        "UPDATE browser_sessions SET steps_json = ? WHERE id = ?",
        (steps_json, session_id),
    )
    return session


async def complete_session(
    db: aiosqlite.Connection,
    session_id: str,
    status: str = "completed",
) -> BrowserSession | None:
    """Mark session as completed or failed."""
    session = await get_session(db, session_id)
    if not session:
        return None
    session = session.model_copy(update={"status": status})
    await db.execute(
        "UPDATE browser_sessions SET status = ? WHERE id = ?",
        (status, session_id),
    )
    return session


async def set_metadata(
    db: aiosqlite.Connection,
    session_id: str,
    metadata: dict,
    *,
    merge: bool = True,
) -> BrowserSession | None:
    """Persist a metadata dict on the session.

    With ``merge=True`` (default) the new keys are merged into existing
    metadata; with ``merge=False`` the metadata is replaced wholesale.
    """
    session = await get_session(db, session_id)
    if not session:
        return None
    new_meta = (dict(session.metadata) if merge else {})
    new_meta.update(metadata or {})
    new_meta_json = json.dumps(new_meta, ensure_ascii=False)
    await db.execute(
        "UPDATE browser_sessions SET metadata_json = ? WHERE id = ?",
        (new_meta_json, session_id),
    )
    return session.model_copy(update={"metadata": new_meta})


async def update_feature_name(
    db: aiosqlite.Connection,
    session_id: str,
    feature_name: str,
) -> BrowserSession | None:
    """Update only the feature_name column on an existing session.

    Used by the orchestrator after deriving a feature name from the first
    snapshot when the session was created with an empty feature_name.
    """
    session = await get_session(db, session_id)
    if not session:
        return None
    await db.execute(
        "UPDATE browser_sessions SET feature_name = ? WHERE id = ?",
        (feature_name, session_id),
    )
    return session.model_copy(update={"feature_name": feature_name})
