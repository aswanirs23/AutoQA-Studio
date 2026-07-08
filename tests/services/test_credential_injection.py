import http.server, socketserver, threading
import pytest
from backend.services.playwright_runner import run_playwright_code

PAGE = """<!doctype html><body><input placeholder="Username" id="u"><input type="password" id="p">
<button id="go">Sign in</button><script>
document.querySelector('#go').addEventListener('click',function(){
 document.body.innerHTML += '<div id="echo">'+document.querySelector('#u').value+'|'+document.querySelector('#p').value+'</div>';
});</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers()
        self.wfile.write(PAGE.encode())
    def log_message(self,*a): pass


@pytest.fixture()
def server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"; httpd.shutdown()


async def test_four_arg_test_receives_injected_credentials(server):
    code = (
        "async def test(page, base_url, username, password):\n"
        "    await page.goto(base_url + '/')\n"
        "    await page.get_by_placeholder('Username').fill(username)\n"
        "    await page.locator(\"input[type='password']\").fill(password)\n"
        "    await page.locator('#go').click()\n"
        "    await page.locator('#echo').wait_for(state='visible', timeout=8000)\n"
        "    txt = await page.locator('#echo').inner_text()\n"
        "    assert txt == 'alice|s3cret', txt\n"
    )
    res = await run_playwright_code(code, server, headless=True, username="alice", password="s3cret")
    assert res["status"] == "passed", res


async def test_two_arg_test_still_runs(server):
    code = ("async def test(page, base_url):\n"
            "    await page.goto(base_url + '/')\n"
            "    assert await page.get_by_placeholder('Username').count() == 1\n")
    res = await run_playwright_code(code, server, headless=True, username="x", password="y")
    assert res["status"] == "passed", res
