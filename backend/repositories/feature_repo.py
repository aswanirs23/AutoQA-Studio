"""Feature CRUD under a project."""

from __future__ import annotations

import uuid

import aiosqlite

from backend.db import fetch_all, fetch_one
from backend.models.test_case import Feature


async def verify_project_owned(db: aiosqlite.Connection, user_id: str, project_id: str) -> bool:
    row = await fetch_one(db, "SELECT 1 FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
    return row is not None


async def get_feature_by_id(db: aiosqlite.Connection, feature_id: str) -> Feature | None:
    """Fetch a feature by id without auth — callers must verify project ownership separately."""
    row = await fetch_one(
        db,
        "SELECT id, project_id, name, description, sort_order FROM features WHERE id = ?",
        (feature_id,),
    )
    if not row:
        return None
    return Feature(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"] or "",
        sort_order=row["sort_order"] or 0,
    )


async def create_feature(
    db: aiosqlite.Connection,
    user_id: str,
    project_id: str,
    name: str,
    description: str = "",
    sort_order: int = 0,
) -> Feature | None:
    if not await verify_project_owned(db, user_id, project_id):
        return None
    fid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO features (id, project_id, name, description, sort_order) VALUES (?, ?, ?, ?, ?)",
        (fid, project_id, name, description, sort_order),
    )
    return Feature(id=fid, project_id=project_id, name=name, description=description, sort_order=sort_order, test_case_count=0)


async def list_features(db: aiosqlite.Connection, user_id: str, project_id: str) -> list[Feature]:
    if not await verify_project_owned(db, user_id, project_id):
        return []
    rows = await fetch_all(
        db,
        """
        SELECT f.id, f.project_id, f.name, f.description, f.sort_order,
          (SELECT COUNT(*) FROM test_cases t WHERE t.feature_id = f.id) AS tc
        FROM features f
        WHERE f.project_id = ?
        ORDER BY f.sort_order, f.name
        """,
        (project_id,),
    )
    return [
        Feature(
            id=r["id"],
            project_id=r["project_id"],
            name=r["name"],
            description=r["description"] or "",
            sort_order=r["sort_order"],
            test_case_count=r["tc"],
        )
        for r in rows
    ]


async def get_feature(
    db: aiosqlite.Connection, user_id: str, project_id: str, feature_id: str
) -> Feature | None:
    if not await verify_project_owned(db, user_id, project_id):
        return None
    row = await fetch_one(
        db,
        """
        SELECT f.id, f.project_id, f.name, f.description, f.sort_order,
          (SELECT COUNT(*) FROM test_cases t WHERE t.feature_id = f.id) AS tc
        FROM features f
        WHERE f.id = ? AND f.project_id = ?
        """,
        (feature_id, project_id),
    )
    if not row:
        return None
    return Feature(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"] or "",
        sort_order=row["sort_order"],
        test_case_count=row["tc"],
    )


async def update_feature(
    db: aiosqlite.Connection,
    user_id: str,
    project_id: str,
    feature_id: str,
    name: str | None = None,
    description: str | None = None,
    sort_order: int | None = None,
) -> bool:
    if not await verify_project_owned(db, user_id, project_id):
        return False
    row = await fetch_one(db, "SELECT id FROM features WHERE id = ? AND project_id = ?", (feature_id, project_id))
    if not row:
        return False
    parts: list[str] = []
    vals: list = []
    if name is not None:
        parts.append("name = ?")
        vals.append(name)
    if description is not None:
        parts.append("description = ?")
        vals.append(description)
    if sort_order is not None:
        parts.append("sort_order = ?")
        vals.append(sort_order)
    if not parts:
        return True
    vals.extend([feature_id, project_id])
    sql = f"UPDATE features SET {', '.join(parts)} WHERE id = ? AND project_id = ?"
    cur = await db.execute(sql, tuple(vals))
    return cur.rowcount > 0


async def delete_feature(db: aiosqlite.Connection, user_id: str, project_id: str, feature_id: str) -> bool:
    if not await verify_project_owned(db, user_id, project_id):
        return False
    cur = await db.execute("DELETE FROM features WHERE id = ? AND project_id = ?", (feature_id, project_id))
    return cur.rowcount > 0
