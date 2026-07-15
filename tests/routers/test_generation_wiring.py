from unittest.mock import AsyncMock, patch

from backend.services import snapshot_cache


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


def test_generate_forwards_captured_snapshot(client, seed_test_case):
    snapshot_cache.clear_snapshot_cache()
    pid = client.post("/api/projects", json={"name": "P3", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify cart shows items")

    seen = {}

    async def fake_capture(base_url, landing_path="", storage_state_path=None, timeout_s=20.0):
        return '- button "Add to cart"'

    async def fake_generate(tc_dict, base_url, settings, **kwargs):
        seen["page_snapshot"] = kwargs.get("page_snapshot")
        return "async def test(page, base_url):\n    pass\n"

    with patch("backend.routers.playwright_exec.capture_page_snapshot", new=fake_capture), \
         patch("backend.routers.playwright_exec.generate_playwright_code", new=fake_generate):
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/generate-playwright",
                         json={"regenerate": True})

    assert r.status_code == 200, r.text
    assert seen["page_snapshot"] == '- button "Add to cart"'


def test_generate_falls_back_when_capture_raises(client, seed_test_case):
    snapshot_cache.clear_snapshot_cache()
    pid = client.post("/api/projects", json={"name": "P4", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify cart shows items")

    seen = {}

    async def boom_capture(*a, **k):
        raise RuntimeError("browser exploded")

    async def fake_generate(tc_dict, base_url, settings, **kwargs):
        seen["page_snapshot"] = kwargs.get("page_snapshot")
        return "async def test(page, base_url):\n    pass\n"

    with patch("backend.routers.playwright_exec.capture_page_snapshot", new=boom_capture), \
         patch("backend.routers.playwright_exec.generate_playwright_code", new=fake_generate):
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/generate-playwright",
                         json={"regenerate": True})

    assert r.status_code == 200, r.text
    assert seen["page_snapshot"] == ""


def test_generate_forwards_authenticated_when_session_exists(client, seed_test_case, monkeypatch, tmp_path):
    from backend.services import playwright_login, snapshot_cache
    import backend.routers.playwright_exec as pexec_mod
    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)
    snapshot_cache.clear_snapshot_cache()
    pid = client.post("/api/projects", json={"name": "PA", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify cart shows items")
    p = playwright_login.auth_storage_path(pid); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("{}")

    seen = {}
    async def fake_capture(*a, **k): return "- button \"Add to cart\""
    async def fake_generate(tc_dict, base_url, settings, **kw):
        seen["authenticated"] = kw.get("authenticated")
        return "async def test(page, base_url):\n    pass\n"
    monkeypatch.setattr(pexec_mod, "capture_page_snapshot", fake_capture)
    monkeypatch.setattr(pexec_mod, "generate_playwright_code", fake_generate)

    r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/generate-playwright", json={"regenerate": True})
    assert r.status_code == 200, r.text
    assert seen["authenticated"] is True


def test_generate_authenticated_false_without_session(client, seed_test_case, monkeypatch, tmp_path):
    from backend.services import playwright_login, snapshot_cache
    import backend.routers.playwright_exec as pexec_mod
    monkeypatch.setattr(playwright_login, "_data_dir", lambda: tmp_path)  # empty dir → no session file
    snapshot_cache.clear_snapshot_cache()
    pid = client.post("/api/projects", json={"name": "PB", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://127.0.0.1:1/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify cart shows items")

    seen = {}
    async def fake_capture(*a, **k): return ""
    async def fake_generate(tc_dict, base_url, settings, **kw):
        seen["authenticated"] = kw.get("authenticated")
        return "async def test(page, base_url):\n    pass\n"
    monkeypatch.setattr(pexec_mod, "capture_page_snapshot", fake_capture)
    monkeypatch.setattr(pexec_mod, "generate_playwright_code", fake_generate)

    r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/generate-playwright", json={"regenerate": True})
    assert r.status_code == 200, r.text
    assert seen["authenticated"] is False
