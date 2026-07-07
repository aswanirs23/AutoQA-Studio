"""Playwright code generation + execution for individual test cases."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import get_effective_settings
from backend.db import get_db
from backend.deps import get_current_user_id
from backend.repositories import project_repo, testcase_repo
from backend.services.llm_service import generate_playwright_code
from backend.services.playwright_login import auth_storage_path, capture_login_session, looks_like_login_page
from backend.services.playwright_runner import run_playwright_code
from backend.services.upstream_errors import map_upstream_exception

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["playwright"])


class GenerateBody(BaseModel):
    # When false (default), a previously stored code is returned as-is so we don't
    # call the LLM again. Set true to force a fresh generation and overwrite it.
    regenerate: bool = False


class GenerateResponse(BaseModel):
    code: str
    cached: bool = False  # True when returned from storage without an LLM call


class RunBody(BaseModel):
    code: str
    headless: bool = True


class SaveCodeBody(BaseModel):
    code: str


class SaveCodeResponse(BaseModel):
    ok: bool = True


class RunResponse(BaseModel):
    status: str  # 'passed' | 'failed' | 'error'
    screenshot_b64: str | None = None
    error_message: str | None = None
    console_log: str = ""
    duration_ms: int = 0


class SuggestExpectedBody(BaseModel):
    actual_page_text: str
    current_expected_result: str
    error_message: str = ""


class SuggestExpectedResponse(BaseModel):
    suggested: str


@router.post("/{project_id}/test-cases/{test_case_id}/generate-playwright", response_model=GenerateResponse)
async def generate_playwright(
    project_id: str,
    test_case_id: str,
    body: GenerateBody | None = None,
    user_id: str = Depends(get_current_user_id),
) -> GenerateResponse:
    body = body or GenerateBody()
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        if not (proj.base_url or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Project base_url is required. Set it in Project Overview.",
            )
        tc = await testcase_repo.get_test_case(db, project_id, test_case_id)
        if not tc:
            raise HTTPException(status_code=404, detail="Test case not found")

    # Reuse previously generated code unless a fresh generation was requested.
    if not body.regenerate and (tc.playwright_code or "").strip():
        return GenerateResponse(code=tc.playwright_code, cached=True)

    # Normalize trailing slash. The LLM-generated code appends '/<path>', so a
    # base_url like "https://x.com/" would produce "//<path>" — many sites
    # accept that URL but serve a minimal empty shell instead of full content.
    base_url = proj.base_url.strip().rstrip("/")
    settings = get_effective_settings()
    tc_dict = {
        "title": tc.title,
        "preconditions": tc.preconditions,
        "steps": tc.steps,
        "expected_result": tc.expected_result,
    }
    try:
        code = await generate_playwright_code(tc_dict, base_url, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("LLM error", e) from e

    # Store so subsequent opens reuse it instead of calling the LLM again.
    try:
        async with get_db() as db:
            await testcase_repo.save_playwright_code(db, project_id, test_case_id, code)
    except Exception:
        logger.exception("save_playwright_code failed for project=%s tc=%s", project_id, test_case_id)

    return GenerateResponse(code=code, cached=False)


@router.post("/{project_id}/test-cases/{test_case_id}/save-playwright", response_model=SaveCodeResponse)
async def save_playwright(
    project_id: str,
    test_case_id: str,
    body: SaveCodeBody,
    user_id: str = Depends(get_current_user_id),
) -> SaveCodeResponse:
    """Persist hand-edited Playwright code for a test case."""
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        tc = await testcase_repo.get_test_case(db, project_id, test_case_id)
        if not tc:
            raise HTTPException(status_code=404, detail="Test case not found")
        await testcase_repo.save_playwright_code(db, project_id, test_case_id, body.code)
    return SaveCodeResponse(ok=True)


@router.post("/{project_id}/test-cases/{test_case_id}/run-playwright", response_model=RunResponse)
async def run_playwright(
    project_id: str,
    test_case_id: str,
    body: RunBody,
    user_id: str = Depends(get_current_user_id),
) -> RunResponse:
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        if not (proj.base_url or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Project base_url is required. Set it in Project Overview.",
            )
        tc = await testcase_repo.get_test_case(db, project_id, test_case_id)
        if not tc:
            raise HTTPException(status_code=404, detail="Test case not found")

    # Normalize trailing slash on base_url. See generate_playwright above for why.
    base_url = proj.base_url.strip().rstrip("/")

    # Resolve the project's saved auth session (if any) so the run executes as a
    # logged-in user. state_arg is None when no session has been captured yet.
    state_path = auth_storage_path(project_id)
    state_arg = str(state_path) if state_path.exists() else None

    async def _run() -> dict:
        # The runner is documented as "never raises for test failures", but
        # unexpected infra errors (tempdir cleanup, subprocess spawn, etc.) can
        # still bubble up — catch them so the client sees a structured error
        # rather than a generic 500.
        try:
            return await run_playwright_code(body.code, base_url, body.headless, storage_state_path=state_arg)
        except Exception as e:
            logger.exception("run_playwright_code raised for project=%s tc=%s", project_id, test_case_id)
            return {
                "status": "error",
                "screenshot_b64": None,
                "error_message": f"Runner crashed: {type(e).__name__}: {e}",
                "console_log": "",
                "duration_ms": 0,
            }

    result = await _run()

    # One auto-relogin + retry if the run looks like it hit a login wall. Capped
    # at a single attempt so a persistently broken login can't loop forever.
    async with get_db() as db:
        auth = await project_repo.get_project_auth(db, user_id, project_id)
    if auth and auth.get("login_url") and auth.get("password"):
        msg = result.get("error_message") or ""
        if result.get("status") != "passed" and looks_like_login_page(base_url, msg, auth["login_url"]):
            cap = await capture_login_session(auth, base_url, project_id)
            if cap.get("ok"):
                state_arg = str(state_path)
                result = await _run()

    # Persist the run result on the test case row. Persistence failures must
    # not lose the run result — log and continue so the client still sees it.
    try:
        async with get_db() as db:
            await testcase_repo.record_test_run(
                db,
                project_id,
                test_case_id,
                result.get("status", "error"),
                result.get("screenshot_b64"),
            )
            # Persist the code that was actually run so edits stick and the next
            # open reuses them instead of regenerating.
            if (body.code or "").strip():
                await testcase_repo.save_playwright_code(db, project_id, test_case_id, body.code)
    except Exception:
        logger.exception("record_test_run failed for project=%s tc=%s", project_id, test_case_id)

    return RunResponse(
        status=result.get("status", "error"),
        screenshot_b64=result.get("screenshot_b64"),
        error_message=result.get("error_message"),
        console_log=result.get("console_log", "") or "",
        duration_ms=int(result.get("duration_ms", 0) or 0),
    )


@router.post("/{project_id}/test-cases/{test_case_id}/suggest-expected-result", response_model=SuggestExpectedResponse)
async def suggest_expected(
    project_id: str,
    test_case_id: str,
    body: SuggestExpectedBody,
    user_id: str = Depends(get_current_user_id),
) -> SuggestExpectedResponse:
    """LLM-rewrite a test case's expected_result to match observed page behavior.

    This endpoint does NOT persist anything. The frontend uses the suggestion as
    the default text in the Update Expected Result modal, and persistence happens
    via the existing PATCH /api/projects/{pid}/test-cases/{tcid} endpoint after
    the user confirms.
    """
    from backend.services.llm_service import suggest_expected_result

    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        tc = await testcase_repo.get_test_case(db, project_id, test_case_id)
        if not tc:
            raise HTTPException(status_code=404, detail="Test case not found")

    settings = get_effective_settings()
    try:
        suggested = await suggest_expected_result(
            current_expected_result=body.current_expected_result,
            actual_page_text=body.actual_page_text,
            error_message=body.error_message,
            settings=settings,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("LLM error", e) from e

    return SuggestExpectedResponse(suggested=suggested)
