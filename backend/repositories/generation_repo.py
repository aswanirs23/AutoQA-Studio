"""generations + generation_inputs persistence.

`create_generation` inserts both tables in one logical write (caller commits).
`list_generations_with_outputs` joins to live test cases by (source_ref, feature_id)
and excludes generations with zero matches (soft hide-when-empty).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite

from backend.db import fetch_all
from backend.models.test_case import Generation, GenerationInput


async def create_generation(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    feature_id: str,
    trigger: str,
    source_ref: str,
    summary: str,
    inputs: list[dict],
) -> Generation:
    """Insert one generations row and N generation_inputs rows. Caller commits."""
    gid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO generations(id, project_id, feature_id, trigger, source_ref, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (gid, project_id, feature_id, trigger, source_ref, summary, now),
    )

    out_inputs: list[GenerationInput] = []
    for idx, raw in enumerate(inputs):
        iid = str(uuid.uuid4())
        st = str(raw.get("source_type") or "").strip() or "unknown"
        url = raw.get("url")
        text_content = raw.get("text_content")
        image_path = raw.get("image_path")
        i_summary = str(raw.get("summary") or "")
        sort_order = int(raw.get("sort_order", idx))
        await db.execute(
            """
            INSERT INTO generation_inputs(
                id, generation_id, source_type, url, text_content, image_path, summary, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (iid, gid, st, url, text_content, image_path, i_summary, sort_order),
        )
        out_inputs.append(GenerationInput(
            id=iid, source_type=st, url=url, text_content=text_content,
            image_path=image_path, summary=i_summary, sort_order=sort_order,
        ))

    return Generation(
        id=gid,
        project_id=project_id,
        feature_id=feature_id,
        trigger=trigger,
        source_ref=source_ref,
        summary=summary,
        created_at=datetime.fromisoformat(now),
        inputs=out_inputs,
    )


async def list_generations_with_outputs(
    db: aiosqlite.Connection,
    feature_id: str,
    limit: int = 100,
) -> list[tuple[Generation, list[str]]]:
    """Return (generation, test_case_ids) tuples newest first.

    Excludes generations whose joined live test case list is empty (soft hide-when-empty).
    """
    rows = await fetch_all(
        db,
        """
        SELECT g.id, g.project_id, g.feature_id, g.trigger, g.source_ref, g.summary, g.created_at
        FROM generations g
        WHERE g.feature_id = ?
        ORDER BY g.created_at DESC
        LIMIT ?
        """,
        (feature_id, limit),
    )

    out: list[tuple[Generation, list[str]]] = []
    for r in rows:
        tc_rows = await fetch_all(
            db,
            "SELECT id FROM test_cases WHERE feature_id = ? AND source_ref = ?",
            (feature_id, r["source_ref"]),
        )
        tc_ids = [t["id"] for t in tc_rows]
        if not tc_ids:
            continue  # hide-when-empty

        input_rows = await fetch_all(
            db,
            """
            SELECT id, source_type, url, text_content, image_path, summary, sort_order
            FROM generation_inputs
            WHERE generation_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (r["id"],),
        )
        inputs = [
            GenerationInput(
                id=ir["id"],
                source_type=ir["source_type"],
                url=ir["url"],
                text_content=ir["text_content"],
                image_path=ir["image_path"],
                summary=ir["summary"] or "",
                sort_order=ir["sort_order"] or 0,
            )
            for ir in input_rows
        ]

        gen = Generation(
            id=r["id"],
            project_id=r["project_id"],
            feature_id=r["feature_id"],
            trigger=r["trigger"],
            source_ref=r["source_ref"] or "",
            summary=r["summary"] or "",
            created_at=datetime.fromisoformat(r["created_at"]),
            inputs=inputs,
        )
        out.append((gen, tc_ids))

    return out


async def get_input_for_image(
    db: aiosqlite.Connection,
    input_id: str,
) -> tuple[str, str, str] | None:
    """Return (image_path, project_id, source_type) for an input row whose image is being requested.

    Caller checks project ownership against the user.
    """
    row = await fetch_all(
        db,
        """
        SELECT gi.image_path, gi.source_type, g.project_id
        FROM generation_inputs gi
        JOIN generations g ON g.id = gi.generation_id
        WHERE gi.id = ?
        """,
        (input_id,),
    )
    if not row:
        return None
    r = row[0]
    if not r["image_path"]:
        return None
    return (r["image_path"], r["project_id"], r["source_type"])
