import http.server, socketserver, threading
from unittest.mock import AsyncMock, patch
import pytest

PAGE = "<!doctype html><body><h1>Dashboard</h1><div id='menu'>Menu open</div></body>"


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE.encode())
    def log_message(self, *a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


def test_full_self_heal_loop(client, seed_test_case, server):
    pid = client.post("/api/projects", json={"name": "Heal", "description": ""}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"base_url": server})
    fid = client.post(f"/api/projects/{pid}/features", json={"name": "F"}).json()["id"]
    tcid = seed_test_case(pid, fid, title="Verify dashboard menu")

    # 1. Run WRONG code -> fails, captures a snapshot.
    bad = ("async def test(page, base_url):\n"
           "    await page.goto(base_url + '/')\n"
           "    assert await page.get_by_text('NONEXISTENT').count() > 0, 'wrong'\n")
    r1 = client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
                     json={"code": bad, "headless": True}).json()
    assert r1["status"] == "failed"
    assert r1["page_snapshot"]  # snapshot captured

    # 2. Heal (LLM stubbed to return corrected code + expected).
    good = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('Menu open').count() > 0, 'menu shows'\n")
    with patch("backend.routers.playwright_exec.heal_test_case", new_callable=AsyncMock) as m:
        m.return_value = ("The dashboard menu is shown", good)
        h = client.post(f"/api/projects/{pid}/test-cases/{tcid}/heal",
                        json={"current_code": bad, "page_snapshot": r1["page_snapshot"],
                              "error_message": r1["error_message"]}).json()
    assert h["suggested_code"] == good

    # 3. Save healed expected + code, then re-run -> now PASSES.
    client.patch(f"/api/projects/{pid}/test-cases/{tcid}",
                 json={"expected_result": h["suggested_expected"]})
    client.post(f"/api/projects/{pid}/test-cases/{tcid}/save-playwright",
                json={"code": h["suggested_code"]})
    r2 = client.post(f"/api/projects/{pid}/test-cases/{tcid}/run-playwright",
                     json={"code": h["suggested_code"], "headless": True}).json()
    assert r2["status"] == "passed", r2
