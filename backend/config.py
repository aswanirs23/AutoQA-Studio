"""Application settings loaded from environment and optional .env file.

Non-secret options (models, ``DATABASE_PATH``, ``JWT_SECRET``, etc.) are read from the
environment / ``.env`` as usual.

**API keys and integration secrets** (see ``SECRET_OVERRIDABLE_KEYS``) are **not** taken
from ``.env`` for runtime use. They are stored only in SQLite via the Settings UI and
merged in ``get_effective_settings()``. Values for those fields in ``.env`` are ignored
so secrets are not kept in environment files.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolved_env_file() -> str:
    """Prefer project-root .env; avoids empty keys when CWD is not the repo root."""
    here = Path(__file__).resolve()
    backend_dir = here.parent
    project_root = backend_dir.parent
    for candidate in (project_root / ".env", backend_dir / ".env"):
        if candidate.is_file():
            return str(candidate)
    return str(project_root / ".env")


class Settings(BaseSettings):
    """Environment-backed configuration; see module docstring for ``.env`` resolution."""

    model_config = SettingsConfigDict(
        env_file=_resolved_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM providers (keys + default models) ---
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "openai"  # openai | anthropic | gemini
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-20250514"
    gemini_model: str = "gemini-2.0-flash"

    # --- Optional parsers (Figma REST, Jira Cloud REST) ---
    figma_access_token: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    # --- App storage and auth ---
    # SQLite path relative to project root (see db.py); override with DATABASE_PATH
    database_path: str = "data/testgen.db"
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    auth_disabled: bool = True  # if True, single local dev user without JWT (set False + JWT_SECRET for multi-user)

    # --- Deployment ---
    log_level: str = "INFO"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # --- Browser explorer (AI-driven exploration → test cases) ---
    browser_explorer_max_actions: int = 60
    browser_explorer_max_pages: int = 25
    browser_explorer_max_seconds: int = 300
    browser_explorer_default_driver: str = "playwright"  # playwright | mcp (mcp is Phase 2)
    browser_explorer_read_only: bool = True
    # Where screenshots from exploration runs are stored (relative to project root)
    browser_explorer_screenshot_dir: str = "data/screenshots"

    # --- Browser MCP driver (browsermcp.io) ---
    # Spawned via stdio. Args are space-separated for easy override.
    # The user must install the browsermcp.io Chrome extension and click
    # "Connect" on a logged-in tab before kicking off an MCP-driven explore.
    browser_mcp_command: str = "npx"
    browser_mcp_args: str = "-y @browsermcp/mcp@latest"
    browser_mcp_startup_timeout_seconds: int = 30
    browser_mcp_tool_timeout_seconds: int = 30


# Keys allowed to be overridden from the Settings UI (stored in app_settings.secrets_json).
SECRET_OVERRIDABLE_KEYS = frozenset(
    {
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "figma_access_token",
        "jira_base_url",
        "jira_email",
        "jira_api_token",
    }
)

# In-memory copy of DB overrides; refreshed at startup and after PUT /api/settings/keys.
SECRET_OVERRIDES: dict[str, str] = {}


def effective_llm_provider(settings: Settings, provider_override: str | None) -> str:
    """Resolve which backend to use for LLM and vision calls.

    If the client sends a **non-empty** provider (e.g. UI dropdown: OpenAI / Anthropic /
    Gemini), that choice is used **exactly** — no automatic fallback to another provider.

    If the provider is omitted (Default), we use ``LLM_PROVIDER`` from the environment,
    and may fall back from OpenAI to Gemini/Anthropic when the OpenAI key is unset in
    app storage but another provider key is configured.
    """
    raw = (provider_override or "").strip()
    if raw:
        return raw.lower()
    p = (settings.llm_provider or "openai").lower()
    if p == "openai" and not settings.openai_api_key:
        if settings.gemini_api_key:
            return "gemini"
        if settings.anthropic_api_key:
            return "anthropic"
    return p


def resolved_model_id(settings: Settings, provider: str, model_override: str | None) -> str:
    """API model id: optional UI override wins, else the env default for that provider."""
    o = (model_override or "").strip()
    if o:
        return o
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    if provider == "gemini":
        return settings.gemini_model
    return settings.openai_model


@lru_cache
def get_settings() -> Settings:
    """Single shared Settings instance for routers and services."""
    return Settings()


def _settings_without_env_secrets() -> Settings:
    """Strip secret fields loaded from env so only SQLite-backed overrides apply."""
    s = get_settings()
    return s.model_copy(update={k: "" for k in SECRET_OVERRIDABLE_KEYS})


def get_effective_settings() -> Settings:
    """Settings with API keys and integration secrets taken only from app storage (SQLite)."""
    base = _settings_without_env_secrets()
    if not SECRET_OVERRIDES:
        return base
    patch = {k: SECRET_OVERRIDES[k] for k in SECRET_OVERRIDABLE_KEYS if k in SECRET_OVERRIDES}
    if not patch:
        return base
    return base.model_copy(update=patch)
