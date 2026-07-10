import http.server, socketserver, threading
import pytest
from backend.services.playwright_runner import run_playwright_code

PAGE = """<!doctype html><body>
<h1>Dashboard</h1><button id="go">Open Menu</button>
<nav aria-label="Main">home</nav></body>"""


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


async def test_failed_run_captures_page_snapshot(server):
    # This assertion is false, so the run fails and must capture a snapshot.
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('NOT PRESENT').count() > 0, 'nope'\n")
    res = await run_playwright_code(code, server, headless=True)
    assert res["status"] == "failed"
    snap = res.get("page_snapshot", "")
    assert snap and ("Open Menu" in snap or "Dashboard" in snap), snap


async def test_passed_run_has_empty_snapshot(server):
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_text('Dashboard').count() > 0\n")
    res = await run_playwright_code(code, server, headless=True)
    assert res["status"] == "passed"
    assert res.get("page_snapshot", "") == ""
