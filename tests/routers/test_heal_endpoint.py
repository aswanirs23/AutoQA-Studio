from unittest.mock import AsyncMock, patch


def test_heal_returns_expected_and_code_from_snapshot(client, seed_test_case):
    pid = client.post("/api/projects", json={"name": "P", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://x/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify nav menu opens")
    with patch("backend.routers.playwright_exec.heal_test_case", new_callable=AsyncMock) as m:
        m.return_value = ("Menu is shown", "async def test(page, base_url):\n    pass\n")
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": "async def test(page, base_url):\n    assert False\n",
                              "page_snapshot": "button: Open Menu", "error_message": "AssertionError"})
    assert r.status_code == 200
    body = r.json()
    assert body["suggested_expected"] == "Menu is shown"
    assert "async def test" in body["suggested_code"]


def test_heal_falls_back_to_text_only_when_no_snapshot(client, seed_test_case):
    pid = client.post("/api/projects", json={"name": "P2", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": "http://x/"})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Nav test")
    with patch("backend.routers.playwright_exec.suggest_expected_result", new_callable=AsyncMock) as ms, \
         patch("backend.routers.playwright_exec.generate_playwright_code", new_callable=AsyncMock) as mg:
        ms.return_value = "Observed text expected"
        mg.return_value = "async def test(page, base_url):\n    pass\n"
        r = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": "x", "page_snapshot": "", "error_message": "boom"})
    assert r.status_code == 200
    assert r.json()["suggested_expected"] == "Observed text expected"
    ms.assert_awaited_once(); mg.assert_awaited_once()  # fallback path used
