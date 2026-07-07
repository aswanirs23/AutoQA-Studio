"""Helpers + session capture for authenticated auto-execute.

Credentials are injected only into the server-assembled login script that runs
in the sandboxed subprocess; they are never returned to clients or written into
generated test code.
"""

from __future__ import annotations

from pathlib import Path

from backend.config import get_settings

_LOGIN_WRAPPER = Path(__file__).with_name("playwright_login_wrapper.py.tmpl")


def _data_dir() -> Path:
    # DATABASE_PATH is like "data/testgen.db"; sessions live alongside it under data/auth/.
    db_path = Path(get_settings().database_path)
    return db_path.parent


def auth_storage_path(project_id: str) -> Path:
    return _data_dir() / "auth" / f"{project_id}.json"


def mask_auth_config(auth: dict) -> dict:
    masked = {k: v for k, v in (auth or {}).items() if k != "password"}
    masked["password_set"] = bool((auth or {}).get("password"))
    return masked


def looks_like_login_page(final_url: str, page_text: str, login_url: str) -> bool:
    if login_url and login_url.rstrip("/") == (final_url or "").rstrip("/"):
        return True
    text = (page_text or "").lower()
    return any(sig in text for sig in ("sign in", "log in", "login", "password"))


def build_login_script(auth: dict, base_url: str, storage_path: str, headless: bool) -> str:
    template = _LOGIN_WRAPPER.read_text(encoding="utf-8")
    sel = auth.get("selectors") or {}
    return template.format(
        login_url=auth.get("login_url", ""),
        username=auth.get("username", ""),
        password=auth.get("password", ""),
        sel_username=sel.get("username", ""),
        sel_password=sel.get("password", ""),
        sel_submit=sel.get("submit", ""),
        success_check=auth.get("success_check", ""),
        base_url=base_url,
        storage_path=storage_path,
        headless=headless,
    )
