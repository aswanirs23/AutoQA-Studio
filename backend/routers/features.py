"""Feature CRUD under a project (nested under ``/api/projects/{project_id}/features``).

Features group test cases for generation and filtering; each test case belongs to exactly one feature.

Also exposes ``generations_router``: read-only per-feature generation history and per-input
image bytes, mounted at ``/api/features/...`` and ``/api/generation-inputs/...``.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.db import get_db
from backend.deps import get_current_user_id
from backend.models.requests import CreateFeatureBody, UpdateFeatureBody
from backend.models.test_case import Feature
from backend.repositories import feature_repo, generation_repo, project_repo

router = APIRouter(prefix="/projects", tags=["features"])

generations_router = APIRouter(tags=["features"])


def _data_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


@generations_router.get("/features/{feature_id}/generations")
async def list_feature_generations(
    feature_id: str,
    user_id: str = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    async with get_db() as db:
        feat = await feature_repo.get_feature_by_id(db, feature_id)
        if not feat:
            raise HTTPException(status_code=404, detail="Feature not found")
        proj = await project_repo.get_project(db, user_id, feat.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Feature not found")
        rows = await generation_repo.list_generations_with_outputs(db, feature_id)

    return [
        {
            "generation": gen.model_dump(mode="json"),
            "test_case_ids": tc_ids,
        }
        for gen, tc_ids in rows
    ]


@generations_router.get("/generation-inputs/{input_id}/image")
async def get_generation_input_image(
    input_id: str,
    user_id: str = Depends(get_current_user_id),
):
    async with get_db() as db:
        info = await generation_repo.get_input_for_image(db, input_id)
        if not info:
            raise HTTPException(status_code=404, detail="Image not found")
        rel_path, project_id, source_type = info
        if source_type != "screenshot":
            raise HTTPException(status_code=404, detail="Image not found")
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Image not found")

    abs_path = (_data_root() / rel_path).resolve()
    expected_root = (_data_root() / "generations").resolve()
    # Path-traversal guard: resolved path must lie inside data/generations/.
    try:
        abs_path.relative_to(expected_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Image not found") from None
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(abs_path)


@router.post("/{project_id}/features", response_model=Feature)
async def create_feature(
    project_id: str,
    body: CreateFeatureBody,
    user_id: str = Depends(get_current_user_id),
) -> Feature:
    async with get_db() as db:
        f = await feature_repo.create_feature(
            db, user_id, project_id, body.name, body.description, body.sort_order
        )
        if not f:
            raise HTTPException(status_code=404, detail="Project not found")
        return f


@router.get("/{project_id}/features", response_model=list[Feature])
async def list_features(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
) -> list[Feature]:
    async with get_db() as db:
        p = await project_repo.get_project(db, user_id, project_id)
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        return await feature_repo.list_features(db, user_id, project_id)


@router.put("/{project_id}/features/{feature_id}", response_model=Feature)
async def update_feature(
    project_id: str,
    feature_id: str,
    body: UpdateFeatureBody,
    user_id: str = Depends(get_current_user_id),
) -> Feature:
    async with get_db() as db:
        ok = await feature_repo.update_feature(
            db,
            user_id,
            project_id,
            feature_id,
            name=body.name,
            description=body.description,
            sort_order=body.sort_order,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Feature or project not found")
        f = await feature_repo.get_feature(db, user_id, project_id, feature_id)
    assert f is not None
    return f


@router.delete("/{project_id}/features/{feature_id}")
async def delete_feature(
    project_id: str,
    feature_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    async with get_db() as db:
        ok = await feature_repo.delete_feature(db, user_id, project_id, feature_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Feature or project not found")
    return {"ok": True}
