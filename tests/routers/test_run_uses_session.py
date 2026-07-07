"""run-playwright resolves the project's saved session and forwards it to the runner."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


def _project_with_tc(client, seed_test_case):
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid)
    return pid, fid, tcid


@patch("backend.routers.playwright_exec.run_playwright_code", new_callable=AsyncMock)
def test_run_passes_storage_state_when_session_exists(mock_run, tmp_path, monkeypatch, client, seed_test_case):
    from backend.services import playwright_login

    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)

    pid, fid, tcid = _project_with_tc(client, seed_test_case)

    # session file present
    p = playwright_login.auth_storage_path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")

    mock_run.return_value = {
        "status": "passed",
        "screenshot_b64": None,
        "error_message": None,
        "console_log": "",
        "duration_ms": 1,
    }
    r = client.post(
        f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
        json={"code": "async def test(page, base_url):\n    pass\n", "headless": True},
    )
    assert r.status_code == 200
    # storage_state_path kwarg was forwarded
    _, kwargs = mock_run.call_args
    assert kwargs.get("storage_state_path", "").endswith(f"{pid}.json")


@patch("backend.services.playwright_login.capture_login_session", new_callable=AsyncMock)
@patch("backend.routers.playwright_exec.run_playwright_code", new_callable=AsyncMock)
def test_run_retries_once_after_relogin_on_apparent_auth_expiry(
    mock_run, mock_capture, tmp_path, monkeypatch, client, seed_test_case
):
    from backend.services import playwright_login

    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)
    # capture_login_session is imported by name into playwright_exec, so patch it there too.
    monkeypatch.setattr("backend.routers.playwright_exec.capture_login_session", mock_capture)

    pid, fid, tcid = _project_with_tc(client, seed_test_case)

    # configure auth so the retry path is eligible
    client.put(
        f"/api/projects/{pid}/auth",
        json={
            "login_url": "http://127.0.0.1:1/login",
            "username": "u",
            "password": "p",
        },
    )

    # first attempt looks like it hit the login wall; second attempt (after
    # relogin) passes.
    mock_run.side_effect = [
        {
            "status": "failed",
            "screenshot_b64": None,
            "error_message": "please sign in to continue",
            "console_log": "",
            "duration_ms": 1,
        },
        {
            "status": "passed",
            "screenshot_b64": None,
            "error_message": None,
            "console_log": "",
            "duration_ms": 1,
        },
    ]
    mock_capture.return_value = {"ok": True, "screenshot_b64": None, "error": None}

    r = client.post(
        f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
        json={"code": "async def test(page, base_url):\n    pass\n", "headless": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "passed"
    assert mock_run.call_count == 2
    assert mock_capture.call_count == 1
