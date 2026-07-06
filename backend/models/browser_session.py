"""Browser session models for the record-and-generate flow."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class SessionStep(BaseModel):
    """One recorded browser action with its captured context."""

    index: int = 0
    instruction: str = ""
    action_type: str = ""  # click, type, navigate, scroll, etc.
    target: str = ""  # human-readable element description
    value: str = ""  # typed text, selected option, etc.
    snapshot_yaml: str = ""  # a11y snapshot before/after the action
    screenshot_b64: str = ""  # base64 screenshot after action
    vision_description: str = ""  # LLM vision description of screenshot
    status: str = "pending"  # pending | running | done | failed
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserSession(BaseModel):
    """Full recorded browser session."""

    id: str
    project_id: str
    user_id: str
    url: str
    feature_name: str = ""
    browser_type: str = "playwright"  # playwright | ide_browser
    steps: list[SessionStep] = Field(default_factory=list)
    status: str = "recording"  # recording | completed | failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Free-form metadata. For AI-explored sessions:
    #   { "mode": "ai_explore", "evidence_ledger": {...}, "goal": "..." }
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Request / Response shapes ---

class StartSessionBody(BaseModel):
    project_id: str
    url: str
    feature_name: str = ""
    browser_type: str = "playwright"
    steps: list[str] = Field(default_factory=list)


class AddStepBody(BaseModel):
    instruction: str
    action_type: str = ""
    target: str = ""
    value: str = ""
    snapshot_yaml: str = ""
    screenshot_b64: str = ""
    vision_description: str = ""
    status: str = "done"
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompleteSessionBody(BaseModel):
    status: str = "completed"


class SessionResponse(BaseModel):
    session: BrowserSession


class SessionListResponse(BaseModel):
    sessions: list[BrowserSession]
