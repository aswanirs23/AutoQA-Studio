"""Test case persistence and hash queries for deduplication."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.db import fetch_all, fetch_one
from backend.models.test_case import TestCase
from backend.services.dedup_service import compute_hash, with_hash


async def _next_tc_index(db: aiosqlite.Connection, project_id: str) -> int:
    """Next TC_### numeric suffix.

    ``test_cases.id`` is a **table-wide** PRIMARY KEY (not scoped per project), so we must
    take MAX across all rows. Per-project COUNT+1 collides with another project's TC_001.
    Using MAX (not COUNT+1) also avoids reuse after deletes within a project.
    """
    _ = project_id  # reserved if we later embed project prefix in ids
    row = await fetch_one(
        db,
        """
        SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) AS m
        FROM test_cases
        WHERE id LIKE 'TC_%' AND LENGTH(id) >= 6
        """,
    )
    if row and row["m"] is not None:
        return int(row["m"]) + 1
    return 1


async def list_test_cases_for_project(db: aiosqlite.Connection, project_id: str) -> list[TestCase]:
    rows = await fetch_all(
        db,
        """
        SELECT t.id, t.project_id, t.feature_id, t.title, t.type, t.preconditions, t.steps,
          t.expected_result, t.priority, t.hash, t.source_ref, t.created_at,
          t.last_run_status, t.last_run_at, t.last_run_screenshot_b64, t.last_run_page_snapshot,
          f.name AS feature_name
        FROM test_cases t
        JOIN features f ON f.id = t.feature_id
        WHERE t.project_id = ?
        ORDER BY t.created_at
        """,
        (project_id,),
    )
    return [_row_to_tc(r) for r in rows]


async def list_test_cases_for_feature(db: aiosqlite.Connection, project_id: str, feature_id: str) -> list[TestCase]:
    rows = await fetch_all(
        db,
        """
        SELECT t.id, t.project_id, t.feature_id, t.title, t.type, t.preconditions, t.steps,
          t.expected_result, t.priority, t.hash, t.source_ref, t.created_at,
          t.last_run_status, t.last_run_at, t.last_run_screenshot_b64, t.last_run_page_snapshot,
          f.name AS feature_name
        FROM test_cases t
        JOIN features f ON f.id = t.feature_id
        WHERE t.project_id = ? AND t.feature_id = ?
        ORDER BY t.created_at
        """,
        (project_id, feature_id),
    )
    return [_row_to_tc(r) for r in rows]


def _row_to_tc(r: aiosqlite.Row) -> TestCase:
    steps = json.loads(r["steps"] or "[]")
    if not isinstance(steps, list):
        steps = []
    ca = r["created_at"]
    created = datetime.fromisoformat(ca.replace("Z", "+00:00")) if ca else None
    # last_run_* fields are nullable + additive; tolerate older rows that lack them
    last_status = None
    try:
        last_status = r["last_run_status"] or None
    except (IndexError, KeyError):
        pass
    lra = None
    try:
        lra_raw = r["last_run_at"]
        if lra_raw:
            lra = datetime.fromisoformat(lra_raw.replace("Z", "+00:00"))
    except (IndexError, KeyError):
        pass
    last_screenshot = None
    try:
        last_screenshot = r["last_run_screenshot_b64"] or None
    except (IndexError, KeyError):
        pass
    last_snapshot = None
    try:
        last_snapshot = r["last_run_page_snapshot"] or None
    except (IndexError, KeyError):
        pass
    # playwright_code is only selected by get_test_case (kept out of list queries
    # to avoid shipping code blobs in every list response); tolerate its absence.
    pw_code = ""
    try:
        pw_code = r["playwright_code"] or ""
    except (IndexError, KeyError):
        pass
    return TestCase(
        id=r["id"],
        project_id=r["project_id"],
        feature_id=r["feature_id"],
        title=r["title"],
        feature=r["feature_name"] or "",
        type=r["type"],
        preconditions=r["preconditions"] or "",
        steps=steps,
        expected_result=r["expected_result"] or "",
        priority=r["priority"],
        hash=r["hash"] or "",
        source_ref=r["source_ref"] or "",
        created_at=created,
        last_run_status=last_status,
        last_run_at=lra,
        last_run_screenshot_b64=last_screenshot,
        last_run_page_snapshot=last_snapshot,
        playwright_code=pw_code,
    )


async def list_test_cases_filtered(
    db: aiosqlite.Connection,
    project_id: str,
    feature_ids: list[str] | None = None,
    search: str | None = None,
    priority: str | None = None,
) -> list[TestCase]:
    """List test cases with optional filters for export."""
    conditions = ["t.project_id = ?"]
    params: list[Any] = [project_id]

    if feature_ids:
        placeholders = ",".join("?" * len(feature_ids))
        conditions.append(f"t.feature_id IN ({placeholders})")
        params.extend(feature_ids)

    if priority:
        conditions.append("t.priority = ?")
        params.append(priority)

    if search:
        conditions.append(
            "(t.title LIKE ? OR t.steps LIKE ? OR t.expected_result LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like])

    where = " AND ".join(conditions)
    rows = await fetch_all(
        db,
        f"""
        SELECT t.id, t.project_id, t.feature_id, t.title, t.type, t.preconditions, t.steps,
          t.expected_result, t.priority, t.hash, t.source_ref, t.created_at,
          t.last_run_status, t.last_run_at, t.last_run_screenshot_b64, t.last_run_page_snapshot,
          f.name AS feature_name
        FROM test_cases t
        JOIN features f ON f.id = t.feature_id
        WHERE {where}
        ORDER BY t.created_at
        """,
        tuple(params),
    )
    return [_row_to_tc(r) for r in rows]


async def existing_hashes_for_project(db: aiosqlite.Connection, project_id: str) -> set[str]:
    """All hashes in project (cross-feature dedup)."""
    rows = await fetch_all(
        db,
        "SELECT hash, title, steps FROM test_cases WHERE project_id = ?",
        (project_id,),
    )
    out: set[str] = set()
    for r in rows:
        if r["hash"]:
            out.add(r["hash"])
        else:
            steps = json.loads(r["steps"] or "[]")
            if not isinstance(steps, list):
                steps = []
            out.add(compute_hash(r["title"], [str(s) for s in steps]))
    return out


async def insert_test_cases(
    db: aiosqlite.Connection,
    project_id: str,
    feature_id: str,
    feature_name: str,
    cases: list[TestCase],
) -> list[TestCase]:
    """Assign TC_### ids and persist; returns saved cases with ids and hashes."""
    next_i = await _next_tc_index(db, project_id)
    saved: list[TestCase] = []
    now = datetime.now(timezone.utc).isoformat()
    for tc in cases:
        tc = with_hash(tc.model_copy(update={"feature": feature_name}))
        tid = f"TC_{next_i:03d}"
        next_i += 1
        steps_json = json.dumps(tc.steps, ensure_ascii=False)
        await db.execute(
            """
            INSERT INTO test_cases (
              id, project_id, feature_id, title, type, preconditions, steps,
              expected_result, priority, hash, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                project_id,
                feature_id,
                tc.title,
                tc.type,
                tc.preconditions,
                steps_json,
                tc.expected_result,
                tc.priority,
                tc.hash,
                tc.source_ref or "",
                now,
            ),
        )
        saved.append(
            tc.model_copy(
                update={
                    "id": tid,
                    "project_id": project_id,
                    "feature_id": feature_id,
                    "feature": feature_name,
                    "created_at": datetime.fromisoformat(now.replace("Z", "+00:00")),
                }
            )
        )
    return saved


async def get_test_case(
    db: aiosqlite.Connection, project_id: str, test_case_id: str
) -> TestCase | None:
    row = await fetch_one(
        db,
        """
        SELECT t.id, t.project_id, t.feature_id, t.title, t.type, t.preconditions, t.steps,
          t.expected_result, t.priority, t.hash, t.source_ref, t.created_at,
          t.last_run_status, t.last_run_at, t.last_run_screenshot_b64, t.last_run_page_snapshot, t.playwright_code,
          f.name AS feature_name
        FROM test_cases t
        JOIN features f ON f.id = t.feature_id
        WHERE t.project_id = ? AND t.id = ?
        """,
        (project_id, test_case_id),
    )
    return _row_to_tc(row) if row else None


async def update_test_case(
    db: aiosqlite.Connection,
    project_id: str,
    test_case_id: str,
    *,
    title: str | None = None,
    type: str | None = None,
    preconditions: str | None = None,
    steps: list[str] | None = None,
    expected_result: str | None = None,
    priority: str | None = None,
    source_ref: str | None = None,
) -> TestCase | None:
    existing = await get_test_case(db, project_id, test_case_id)
    if not existing:
        return None
    new_title = title if title is not None else existing.title
    new_type = type if type is not None else existing.type
    new_pre = preconditions if preconditions is not None else existing.preconditions
    new_steps = steps if steps is not None else existing.steps
    new_exp = expected_result if expected_result is not None else existing.expected_result
    new_pri = priority if priority is not None else existing.priority
    new_ref = source_ref if source_ref is not None else existing.source_ref
    tc = existing.model_copy(
        update={
            "title": new_title,
            "type": new_type,
            "preconditions": new_pre,
            "steps": new_steps,
            "expected_result": new_exp,
            "priority": new_pri,
            "source_ref": new_ref,
        }
    )
    tc = with_hash(tc)
    steps_json = json.dumps(tc.steps, ensure_ascii=False)
    await db.execute(
        """
        UPDATE test_cases SET
          title = ?, type = ?, preconditions = ?, steps = ?,
          expected_result = ?, priority = ?, hash = ?, source_ref = ?
        WHERE project_id = ? AND id = ?
        """,
        (
            tc.title,
            tc.type,
            tc.preconditions,
            steps_json,
            tc.expected_result,
            tc.priority,
            tc.hash,
            tc.source_ref or "",
            project_id,
            test_case_id,
        ),
    )
    return await get_test_case(db, project_id, test_case_id)


async def delete_test_case(db: aiosqlite.Connection, project_id: str, test_case_id: str) -> bool:
    cur = await db.execute(
        "DELETE FROM test_cases WHERE project_id = ? AND id = ?",
        (project_id, test_case_id),
    )
    return cur.rowcount > 0


async def delete_test_cases_bulk(
    db: aiosqlite.Connection, project_id: str, ids: list[str]
) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    sql = f"DELETE FROM test_cases WHERE project_id = ? AND id IN ({placeholders})"
    cur = await db.execute(sql, (project_id, *ids))
    return int(cur.rowcount or 0)


async def aggregate_stats(db: aiosqlite.Connection, project_id: str) -> dict[str, Any]:
    """Counts by type, priority, and per-feature for dashboard."""
    rows = await fetch_all(
        db,
        """
        SELECT t.type, t.priority, t.feature_id, f.name AS feature_name
        FROM test_cases t
        JOIN features f ON f.id = t.feature_id
        WHERE t.project_id = ?
        """,
        (project_id,),
    )
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_feature: dict[str, dict[str, Any]] = {}
    total = 0
    for r in rows:
        total += 1
        typ = str(r["type"] or "happy")
        by_type[typ] = by_type.get(typ, 0) + 1
        pr = str(r["priority"] or "medium")
        by_priority[pr] = by_priority.get(pr, 0) + 1
        fid = r["feature_id"]
        fn = r["feature_name"] or ""
        if fid not in by_feature:
            by_feature[fid] = {"feature_id": fid, "name": fn, "count": 0}
        by_feature[fid]["count"] += 1
    return {
        "total": total,
        "by_type": by_type,
        "by_priority": by_priority,
        "by_feature": list(by_feature.values()),
    }


async def save_playwright_code(
    db: aiosqlite.Connection,
    project_id: str,
    test_case_id: str,
    code: str,
) -> bool:
    """Persist the auto-execute Playwright code for a test case so it can be
    reused instead of regenerated via the LLM on every open."""
    cur = await db.execute(
        "UPDATE test_cases SET playwright_code = ? WHERE project_id = ? AND id = ?",
        (code, project_id, test_case_id),
    )
    return cur.rowcount > 0


async def record_test_run(
    db: aiosqlite.Connection,
    project_id: str,
    test_case_id: str,
    status: str,
    screenshot_b64: str | None,
    page_snapshot: str | None = None,
) -> bool:
    """Persist the result of a Playwright run on a single test case.

    status is one of 'passed', 'failed', 'error'. screenshot_b64 may be None when
    the run errored before a screenshot could be captured. page_snapshot is the
    accessibility snapshot captured when the test ran against a live page — its
    presence is what lets the self-heal button reappear after a reload.
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = await db.execute(
        """
        UPDATE test_cases
        SET last_run_status = ?, last_run_at = ?, last_run_screenshot_b64 = ?, last_run_page_snapshot = ?
        WHERE project_id = ? AND id = ?
        """,
        (status, now, screenshot_b64, page_snapshot or None, project_id, test_case_id),
    )
    return cur.rowcount > 0
