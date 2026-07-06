"""Core domain models: users, projects, features, test cases, input history."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class User(BaseModel):
    """Registered user (password never returned)."""

    id: str
    name: str
    email: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Project(BaseModel):
    """Top-level workspace with editable JSON context."""

    id: str
    user_id: str
    name: str
    description: str = ""
    base_url: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Feature(BaseModel):
    """Feature/module under a project."""

    id: str
    project_id: str
    name: str
    description: str = ""
    sort_order: int = 0
    test_case_count: int = 0


class TestCase(BaseModel):
    """Manual test; hash from title + steps for deduplication within a project."""

    id: str
    project_id: str = ""
    feature_id: str = ""
    title: str
    feature: str = ""  # feature name (for LLM/export)
    type: str = "happy"  # happy | edge | negative
    preconditions: str = ""
    steps: list[str] = Field(default_factory=list)
    expected_result: str = ""
    priority: str = "medium"
    hash: str = ""
    source_ref: str = ""  # traceability: Jira key, Figma URL, etc.
    created_at: datetime | None = None
    last_run_status: str | None = None  # 'passed' | 'failed' | 'error' | None
    last_run_at: datetime | None = None
    last_run_screenshot_b64: str | None = None
    playwright_code: str = ""  # cached auto-execute code; only populated by get_test_case


class InputRecord(BaseModel):
    """Audit trail of generation/iteration per project."""

    id: str = ""
    project_id: str = ""
    feature_id: str | None = None
    source_type: str
    summary: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationInput(BaseModel):
    """One input source within a generation (Jira URL, Figma URL, text, image, browser session)."""

    id: str
    source_type: str  # jira | figma | screenshot | text | browser_session
    url: str | None = None
    text_content: str | None = None
    image_path: str | None = None  # relative to data/
    summary: str = ""
    sort_order: int = 0


class Generation(BaseModel):
    """One generate/iterate action that produced at least one test case for a feature."""

    id: str
    project_id: str
    feature_id: str
    trigger: str  # 'generate' | 'iterate'
    source_ref: str = ""
    summary: str = ""
    created_at: datetime
    inputs: list[GenerationInput] = Field(default_factory=list)
