from unittest.mock import AsyncMock, patch


def test_run_forwards_logged_out_and_creds_for_login_test(client, seed_test_case, monkeypatch, tmp_path):
    from backend.services import playwright_login
    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    client.put(f"/api/projects/{pid}/auth", json={"login_url": "http://127.0.0.1:1/login", "username": "u", "password": "p"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify valid login grants access")
    # session file exists but login test must run logged OUT (state None) with creds
    p = playwright_login.auth_storage_path(pid); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("{}")
    with patch("backend.routers.playwright_exec.run_playwright_code", new_callable=AsyncMock) as m:
        m.return_value = {"status": "passed", "screenshot_b64": None, "error_message": None, "console_log": "", "duration_ms": 1}
        client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright", json={"code": "async def test(page, base_url, username, password):\n    pass\n"})
        _, kw = m.call_args
        assert kw.get("storage_state_path") is None      # logged-out for a login test
        assert kw.get("username") == "u" and kw.get("password") == "p"


def test_save_auth_persists_home_path(client):
    pid = client.post("/api/projects", json={"name": "P2", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}/auth", json={"login_url": "http://x/login", "username": "u", "password": "p", "home_path": "/inventory.html"})
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert got["home_path"] == "/inventory.html"
