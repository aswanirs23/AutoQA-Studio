"""API request and response shapes (Pydantic) used by FastAPI routes."""

from typing import Any

from pydantic import BaseModel, Field

from backend.models.test_case import Feature, Project, TestCase, User


# --- Auth ---
class RegisterBody(BaseModel):
    name: str = "User"
    email: str
    password: str = Field(..., min_length=6)


class LoginBody(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User


# --- Projects ---
class CreateProjectBody(BaseModel):
    name: str = "Untitled project"
    description: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    base_url: str | None = None


class UpdateContextBody(BaseModel):
    context: dict[str, Any]


class ProjectSummaryResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    user_id: str
    feature_count: int = 0
    test_case_count: int = 0
    updated_at: str = ""


class ProjectDetailResponse(BaseModel):
    project: Project
    features: list[Feature]


# --- Features ---
class CreateFeatureBody(BaseModel):
    name: str
    description: str = ""
    sort_order: int = 0


class UpdateFeatureBody(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


# --- Test cases ---
class UpdateTestCaseBody(BaseModel):
    title: str | None = None
    type: str | None = None
    preconditions: str | None = None
    steps: list[str] | None = None
    expected_result: str | None = None
    priority: str | None = None
    source_ref: str | None = None


class BulkDeleteTestCasesBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


class ProjectStatsResponse(BaseModel):
    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    by_feature: list[dict[str, Any]] = Field(default_factory=list)


# --- Generate ---
class GenerateInputItem(BaseModel):
    """One parser payload in a combined generation request."""

    input_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    file_index: int | None = None  # multipart only: index into uploaded `files` list


class GenerateJsonBody(BaseModel):
    """Single-source body uses input_type + data. Multi-source uses inputs (non-empty array)."""

    input_type: str | None = None
    project_id: str
    feature_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    inputs: list[GenerateInputItem] | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    min_test_cases: int | None = None
    preferred_test_types: list[str] | None = None


class GenerateIterateBody(BaseModel):
    project_id: str
    instruction: str
    feature_id: str | None = None  # None = all features in project
    feature_filter: str | None = None
    type_filter: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    min_test_cases: int | None = None
    preferred_test_types: list[str] | None = None


class GenerateResponse(BaseModel):
    added_count: int
    skipped_duplicate_count: int
    test_cases: list[TestCase]
    parsed_summary: str


class ParsersListResponse(BaseModel):
    parsers: list[dict[str, Any]]
