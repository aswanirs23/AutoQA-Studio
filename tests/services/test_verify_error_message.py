# tests/services/test_verify_error_message.py
import http.server, socketserver, threading
import pytest
from backend.services.playwright_login import capture_login_session

LOGIN = """<!doctype html><form><input placeholder="Username" id="u"><input type="password" id="p">
<button id="go">Sign in</button><script>document.querySelector('#go').addEventListener('click',function(e){
e.preventDefault(); if(document.querySelector('#p').value==='secret'){location.href='/home';}});</script></form>"""
HOME = "<!doctype html><body><h1>Welcome</h1></body>"


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = LOGIN if self.path.startswith("/login") else HOME
        self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self,*a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


async def test_error_names_reached_url_and_missing_check(server, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.playwright_login._data_dir", lambda: tmp_path)
    auth = {"login_url": server + "/login", "username": "u", "password": "secret",
            "selectors": {}, "success_check": "NONEXISTENT_TEXT"}
    res = await capture_login_session(auth, base_url=server, project_id="p")
    assert res["ok"] is False
    assert "/home" in res["error"] and "NONEXISTENT_TEXT" in res["error"]
