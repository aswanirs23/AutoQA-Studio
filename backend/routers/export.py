"""Export project or feature test cases to Excel, CSV, JSON, Markdown, or TestRail CSV.

Query ``?format=`` selects the serializer; optional ``feature_id`` limits rows to one feature.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.db import get_db
from backend.deps import get_current_user_id
from backend.repositories import feature_repo, project_repo, testcase_repo
from backend.services.export_service import export_test_cases

router = APIRouter(tags=["export"])

ExportFmt = Literal["excel", "csv", "json", "markdown", "testrail"]


@router.get("/export/{project_id}")
async def export_project_file(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    format: ExportFmt = Query("excel", alias="format"),
    feature_id: str | None = Query(None),
    feature_ids: str | None = Query(None, description="Comma-separated feature IDs"),
    search: str | None = Query(None),
    priority: str | None = Query(None),
) -> Response:
    async with get_db() as db:
        p = await project_repo.get_project(db, user_id, project_id)
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        pname = p.name or ""

        fid_list: list[str] | None = None
        if feature_ids:
            fid_list = [fid.strip() for fid in feature_ids.split(",") if fid.strip()]
        elif feature_id:
            fid_list = [feature_id]

        if fid_list or search or priority:
            cases = await testcase_repo.list_test_cases_filtered(
                db, project_id, feature_ids=fid_list, search=search, priority=priority
            )
        elif feature_id:
            cases = await testcase_repo.list_test_cases_for_feature(db, project_id, feature_id)
        else:
            cases = await testcase_repo.list_test_cases_for_project(db, project_id)

        suffix = "_filtered" if (fid_list or search or priority) else ""

    body, mime, ext = export_test_cases(cases, format, project_name=pname)
    filename = f"test_cases_{project_id[:8]}{suffix}.{ext}"
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/{project_id}/{feature_id}")
async def export_feature_file(
    project_id: str,
    feature_id: str,
    user_id: str = Depends(get_current_user_id),
    format: ExportFmt = Query("excel", alias="format"),
) -> Response:
    async with get_db() as db:
        p = await project_repo.get_project(db, user_id, project_id)
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        f = await feature_repo.get_feature(db, user_id, project_id, feature_id)
        if not f:
            raise HTTPException(status_code=404, detail="Feature not found")
        cases = await testcase_repo.list_test_cases_for_feature(db, project_id, feature_id)

    body, mime, ext = export_test_cases(cases, format, project_name=p.name or "")
    filename = f"test_cases_{project_id[:8]}_{feature_id[:8]}.{ext}"
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
