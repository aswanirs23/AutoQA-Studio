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


async def capture_login_session(auth: dict, base_url: str, project_id: str,
                                headless: bool = True) -> dict:
    """Run the server-assembled login script in the sandbox and persist storage_state.

    Returns {"ok": bool, "screenshot_b64": str|None, "error": str|None}.
    """
    import asyncio as _asyncio
    from backend.services.playwright_runner import _run_script_blocking, _validate_url

    ok_url, err = _validate_url(auth.get("login_url") or "")
    if not ok_url:
        return {"ok": False, "screenshot_b64": None, "error": f"Login URL invalid: {err}"}

    path = auth_storage_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = build_login_script(auth, base_url, str(path), headless)
    result = await _asyncio.to_thread(_run_script_blocking, script)
    if result.get("_timeout"):
        return {"ok": False, "screenshot_b64": None, "error": "Login timed out (60s)."}
    if "ok" not in result:
        return {"ok": False, "screenshot_b64": None,
                "error": f"Login runner error: {result.get('_stderr') or result.get('_parse_error') or 'unknown'}"}
    return {"ok": bool(result.get("ok")), "screenshot_b64": result.get("screenshot_b64"),
            "error": result.get("error")}


_LOGIN_CUES = ("sign in", "sign-in", "signin", "log in", "log-in", "login")


def resolve_landing_path(auth_config: dict) -> str:
    """Path an authenticated test should open, or '' to mean '/'."""
    cfg = auth_config or {}
    home = (cfg.get("home_path") or "").strip()
    if home:
        return home
    success = (cfg.get("success_check") or "").strip()
    if success.startswith("/"):
        return success
    return ""


def is_login_test(title: str, steps: list[str]) -> bool:
    """Heuristic: does this test exercise a login/sign-in flow?"""
    haystack = (title or "").lower()
    for s in steps or []:
        haystack += "\n" + str(s).lower()
    return any(cue in haystack for cue in _LOGIN_CUES)
