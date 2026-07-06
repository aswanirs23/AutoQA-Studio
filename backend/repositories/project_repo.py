"""Project CRUD — always scoped by user_id."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from backend.db import fetch_all, fetch_one
from backend.models.test_case import Project


async def create_project(
    db: aiosqlite.Connection,
    user_id: str,
    name: str,
    description: str = "",
    context: dict | None = None,
) -> Project:
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    ctx = json.dumps(context or {}, ensure_ascii=False)
    await db.execute(
        "INSERT INTO projects (id, user_id, name, description, context, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, user_id, name, description, ctx, now, now),
    )
    return Project(
        id=pid,
        user_id=user_id,
        name=name,
        description=description,
        context=context or {},
        created_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
    )


async def get_project(db: aiosqlite.Connection, user_id: str, project_id: str) -> Project | None:
    row = await fetch_one(
        db,
        "SELECT id, user_id, name, description, base_url, context, created_at, updated_at FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    )
    if not row:
        return None
    ctx = json.loads(row["context"] or "{}")
    return Project(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        description=row["description"] or "",
        base_url=row["base_url"] or "",
        context=ctx,
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
    )


async def list_projects(db: aiosqlite.Connection, user_id: str) -> list[dict]:
    """Rows: id, name, description, updated_at iso, feature_count, test_case_count."""
    rows = await fetch_all(
        db,
        """
        SELECT p.id, p.name, p.description, p.updated_at,
          (SELECT COUNT(*) FROM features f WHERE f.project_id = p.id) AS fc,
          (SELECT COUNT(*) FROM test_cases t WHERE t.project_id = p.id) AS tc
        FROM projects p
        WHERE p.user_id = ?
        ORDER BY p.updated_at DESC
        """,
        (user_id,),
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"] or "",
            "updated_at": r["updated_at"],
            "fc": r["fc"],
            "tc": r["tc"],
        }
        for r in rows
    ]


async def update_project(
    db: aiosqlite.Connection,
    user_id: str,
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    base_url: str | None = None,
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    sets: list[str] = ["updated_at = ?"]
    params: list[str] = [now]
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if base_url is not None:
        sets.append("base_url = ?")
        params.append(base_url)
    params.extend([project_id, user_id])
    cur = await db.execute(
        f"UPDATE projects SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
        tuple(params),
    )
    return cur.rowcount > 0


async def update_project_context(db: aiosqlite.Connection, user_id: str, project_id: str, context: dict) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    ctx = json.dumps(context, ensure_ascii=False)
    cur = await db.execute(
        "UPDATE projects SET context = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (ctx, now, project_id, user_id),
    )
    return cur.rowcount > 0


async def delete_project(db: aiosqlite.Connection, user_id: str, project_id: str) -> bool:
    cur = await db.execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
    return cur.rowcount > 0
