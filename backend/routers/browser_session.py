"""Browser session recording + AI exploration API.

Endpoints let the frontend (or the Cursor agent) create a session, add recorded
steps incrementally, mark the session complete, and list/retrieve sessions.
The recorded session is later consumed by the ``browser_session`` parser plugin
during the normal ``POST /api/generate`` flow.

The ``/explore`` endpoints run an AI-driven exploration in the background,
populating the session's metadata with an Evidence Ledger that the parser
embeds into the LLM prompt for citation-required test case authoring.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import get_effective_settings
from backend.db import get_db
from backend.deps import get_current_user_id
from backend.models.browser_session import (
    AddStepBody,
    CompleteSessionBody,
    SessionListResponse,
    SessionResponse,
    SessionStep,
    StartSessionBody,
)
from backend.repositories import project_repo
from backend.services import browser_session as bs_service
from backend.services.browser_explorer import run_exploration
from backend.services.browser_explorer.ledger import ExplorationLedger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browser-session", tags=["browser-session"])


# In-memory tracker for running exploration tasks (per-process).
# Keyed by session_id. Survives until process restart; on restart the
# session row in DB still has its evidence_ledger up to the last persist.
_exploration_runs: dict[str, dict[str, Any]] = {}


class ExploreStartBody(BaseModel):
    goal: str = ""              # optional — orchestrator derives from page if empty
    max_actions: int | None = None
    max_pages: int | None = None
    max_seconds: int | None = None
    driver: str | None = None  # "playwright" | "mcp"
    read_only: bool | None = None
    headless: bool = True


class ExploreStartResponse(BaseModel):
    session_id: str
    status: str  # "running"


class ExploreStatusResponse(BaseModel):
    session_id: str
    status: str  # "running" | "done" | "error" | "cancelled" | "not_found"
    error: str | None = None
    actions_taken: int = 0
    pages_visited: int = 0
    elapsed_seconds: float = 0.0
    current_url: str = ""
    last_action: str | None = None
    stop_reason: str | None = None
    pages_count: int = 0
    actions_count: int = 0
    errors_count: int = 0


def _summary_from_ledger(ledger_dict: dict) -> dict:
    return {
        "pages_count": len(ledger_dict.get("pages") or []),
        "actions_count": len(ledger_dict.get("actions") or []),
        "errors_count": len(ledger_dict.get("errors_observed") or []),
        "current_url": (ledger_dict.get("pages") or [{}])[-1].get("url", "") if ledger_dict.get("pages") else "",
        "last_action": (ledger_dict.get("actions") or [{}])[-1].get("type") if ledger_dict.get("actions") else None,
        "stop_reason": ledger_dict.get("stop_reason"),
    }


@router.post("/start", response_model=SessionResponse)
async def start_session(
    body: StartSessionBody,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, body.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")

        session = await bs_service.create_session(
            db,
            project_id=body.project_id,
            user_id=user_id,
            url=body.url,
            feature_name=body.feature_name,
            browser_type=body.browser_type,
            initial_steps=body.steps or None,
        )
    return SessionResponse(session=session)


@router.get("/project/{project_id}", response_model=SessionListResponse)
async def list_sessions(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
) -> SessionListResponse:
    async with get_db() as db:
        sessions = await bs_service.list_sessions(db, project_id)
    return SessionListResponse(sessions=sessions)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    async with get_db() as db:
        session = await bs_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(session=session)


@router.post("/{session_id}/step", response_model=SessionResponse)
async def add_step(
    session_id: str,
    body: AddStepBody,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    step = SessionStep(
        instruction=body.instruction,
        action_type=body.action_type,
        target=body.target,
        value=body.value,
        snapshot_yaml=body.snapshot_yaml,
        screenshot_b64=body.screenshot_b64,
        vision_description=body.vision_description,
        status=body.status,
        error=body.error,
        metadata=body.metadata,
    )
    async with get_db() as db:
        session = await bs_service.add_step(db, session_id, step)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(session=session)


@router.put("/{session_id}/step/{step_index}", response_model=SessionResponse)
async def update_step(
    session_id: str,
    step_index: int,
    body: AddStepBody,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    updates = body.model_dump(exclude_unset=True)
    async with get_db() as db:
        session = await bs_service.update_step(db, session_id, step_index, updates)
    if not session:
        raise HTTPException(status_code=404, detail="Session or step not found")
    return SessionResponse(session=session)


@router.post("/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: str,
    body: CompleteSessionBody | None = None,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    status = (body.status if body else None) or "completed"
    if status not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail="status must be 'completed' or 'failed'")
    async with get_db() as db:
        session = await bs_service.complete_session(db, session_id, status)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(session=session)


# ---------------------------------------------------------------------------
# AI exploration endpoints
# ---------------------------------------------------------------------------


@router.post("/{session_id}/explore", response_model=ExploreStartResponse)
async def start_exploration(
    session_id: str,
    body: ExploreStartBody,
    user_id: str = Depends(get_current_user_id),
) -> ExploreStartResponse:
    """Kick off AI-driven exploration in a background task.

    The session must already exist (created via ``POST /start``). Returns
    immediately; poll ``GET /{session_id}/explore/status`` for progress.
    """
    async with get_db() as db:
        session = await bs_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Don't double-run.
    existing = _exploration_runs.get(session_id)
    if existing and existing.get("status") == "running":
        raise HTTPException(status_code=409, detail="Exploration already running for this session")

    settings = get_effective_settings()
    if not (settings.anthropic_api_key or settings.openai_api_key):
        raise HTTPException(
            status_code=400,
            detail="An LLM API key is required for AI exploration (OpenAI or Anthropic). Add one in Settings.",
        )

    state: dict[str, Any] = {
        "status": "running",
        "error": None,
        "ledger_summary": {},
        "task": None,
    }
    _exploration_runs[session_id] = state

    async def _persist_progress(ledger: ExplorationLedger) -> None:
        led_dict = ledger.to_dict()
        state["ledger_summary"] = _summary_from_ledger(led_dict)
        try:
            async with get_db() as db:
                await bs_service.set_metadata(
                    db,
                    session_id,
                    {"mode": "ai_explore", "goal": body.goal, "evidence_ledger": led_dict},
                )
                await db.commit()
        except Exception:
            logger.exception("failed to persist exploration progress for %s", session_id)

    async def _run() -> None:
        try:
            ledger, result = await run_exploration(
                session_id=session_id,
                goal=body.goal,
                starting_url=session.url,
                settings=settings,
                driver_name=body.driver,
                read_only=body.read_only,
                max_actions=body.max_actions,
                max_pages=body.max_pages,
                max_seconds=body.max_seconds,
                headless=body.headless,
                on_progress=_persist_progress,
            )
            # Final persist + complete the session.
            async with get_db() as db:
                await bs_service.set_metadata(
                    db,
                    session_id,
                    {
                        "mode": "ai_explore",
                        "goal": body.goal,
                        "evidence_ledger": ledger.to_dict(),
                        "tool_loop_result": {
                            "stopped": result.stopped,
                            "turns": result.turns,
                            "last_tool": result.last_tool,
                            "error": result.error,
                        },
                    },
                )
                final_status = "completed" if result.stopped == "done" else "failed"
                await bs_service.complete_session(db, session_id, final_status)
                await db.commit()
            state["status"] = "done"
            state["ledger_summary"] = _summary_from_ledger(ledger.to_dict())
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            try:
                async with get_db() as db:
                    await bs_service.complete_session(db, session_id, "failed")
                    await db.commit()
            except Exception:
                logger.exception("failed to mark cancelled session as failed")
            raise
        except Exception as e:
            logger.exception("exploration failed for %s", session_id)
            state["status"] = "error"
            state["error"] = f"{type(e).__name__}: {e}"
            try:
                async with get_db() as db:
                    await bs_service.complete_session(db, session_id, "failed")
                    await db.commit()
            except Exception:
                logger.exception("failed to mark errored session as failed")

    state["task"] = asyncio.create_task(_run())
    return ExploreStartResponse(session_id=session_id, status="running")


@router.get("/{session_id}/explore/status", response_model=ExploreStatusResponse)
async def exploration_status(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> ExploreStatusResponse:
    state = _exploration_runs.get(session_id)
    # Even if the in-memory state is gone (process restart), we can derive
    # status from the persisted session metadata.
    if state is None:
        async with get_db() as db:
            session = await bs_service.get_session(db, session_id)
        if not session:
            return ExploreStatusResponse(session_id=session_id, status="not_found")
        led = (session.metadata or {}).get("evidence_ledger") or {}
        summary = _summary_from_ledger(led)
        derived_status = "done" if session.status == "completed" else "error" if session.status == "failed" else "running"
        return ExploreStatusResponse(
            session_id=session_id,
            status=derived_status,
            **summary,
        )

    summary = state.get("ledger_summary") or {}
    return ExploreStatusResponse(
        session_id=session_id,
        status=state.get("status") or "running",
        error=state.get("error"),
        pages_count=summary.get("pages_count", 0),
        actions_count=summary.get("actions_count", 0),
        errors_count=summary.get("errors_count", 0),
        current_url=summary.get("current_url", ""),
        last_action=summary.get("last_action"),
        stop_reason=summary.get("stop_reason"),
        actions_taken=summary.get("actions_count", 0),
        pages_visited=summary.get("pages_count", 0),
    )


@router.post("/{session_id}/explore/cancel")
async def exploration_cancel(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    state = _exploration_runs.get(session_id)
    if not state or not state.get("task"):
        raise HTTPException(status_code=404, detail="No running exploration for this session")
    task: asyncio.Task = state["task"]
    if task.done():
        return {"status": state.get("status") or "done"}
    task.cancel()
    return {"status": "cancelling"}
