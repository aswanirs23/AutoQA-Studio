import http.server
import socketserver
import threading
import pytest
from pathlib import Path

from backend.services.playwright_login import capture_login_session, auth_storage_path
from backend.services.playwright_runner import run_playwright_code

LOGIN_HTML = """<!doctype html><form>
<input placeholder="Username" id="u"><input type="password" id="p">
<button type="submit" id="go">Sign in</button>
<script>
document.querySelector('#go').addEventListener('click', function(e){
  e.preventDefault();
  if (document.querySelector('#p').value === 'secret') {
    localStorage.setItem('authed','1'); location.href='/protected';
  } else { document.body.innerHTML += '<p>Invalid</p>'; }
});
</script></form>"""

PROTECTED_HTML = """<!doctype html><body><script>
if (localStorage.getItem('authed')==='1'){document.body.innerHTML='<h1>Welcome dashboard</h1>';}
else {location.href='/login';}
</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = LOGIN_HTML if self.path.startswith("/login") else PROTECTED_HTML
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


async def test_capture_then_authenticated_run(server, tmp_path, monkeypatch):
    pid = "testproj"
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "secret",
            "selectors": {}, "success_check": "/protected"}
    res = await capture_login_session(auth, base_url=server, project_id=pid)
    assert res["ok"] is True, res
    assert auth_storage_path(pid).exists()

    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/protected')\n"
            "    await page.get_by_text('Welcome dashboard').first.wait_for(state='visible', timeout=8000)\n"
            "    assert await page.get_by_text('Welcome dashboard').first.count() > 0, 'not authed'\n")
    run = await run_playwright_code(code, server, headless=True,
                                    storage_state_path=str(auth_storage_path(pid)))
    assert run["status"] == "passed", run


async def test_capture_wrong_password_fails_and_writes_nothing(server, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "WRONG",
            "selectors": {}, "success_check": "/protected"}
    res = await capture_login_session(auth, base_url=server, project_id="p2")
    assert res["ok"] is False
    assert not auth_storage_path("p2").exists()
