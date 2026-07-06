"""Input history: one row per generation or iterate run (dashboard + traceability)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from backend.db import fetch_all
from backend.models.test_case import InputRecord


async def add_input_record(
    db: aiosqlite.Connection,
    project_id: str,
    source_type: str,
    summary: str,
    metadata: dict,
    feature_id: str | None = None,
) -> InputRecord:
    from backend.services.parsers.base import strip_internal_metadata

    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    safe_meta = strip_internal_metadata(metadata)
    meta = json.dumps(safe_meta, ensure_ascii=False)
    await db.execute(
        """
        INSERT INTO input_history (id, project_id, feature_id, source_type, summary, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (rid, project_id, feature_id, source_type, summary, meta, now),
    )
    return InputRecord(
        id=rid,
        project_id=project_id,
        feature_id=feature_id,
        source_type=source_type,
        summary=summary,
        at=datetime.fromisoformat(now.replace("Z", "+00:00")),
        metadata=safe_meta,
    )


async def list_input_history(
    db: aiosqlite.Connection, project_id: str, limit: int = 100
) -> list[InputRecord]:
    rows = await fetch_all(
        db,
        """
        SELECT id, project_id, feature_id, source_type, summary, metadata, created_at
        FROM input_history
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    )
    out: list[InputRecord] = []
    for r in rows:
        meta = json.loads(r["metadata"] or "{}")
        out.append(
            InputRecord(
                id=r["id"],
                project_id=r["project_id"],
                feature_id=r["feature_id"],
                source_type=r["source_type"],
                summary=r["summary"],
                at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
                metadata=meta if isinstance(meta, dict) else {},
            )
        )
    return out
