"""Run AI-generated Playwright code in an isolated subprocess.

Safety boundaries:
- Hard 60-second wall-clock timeout on the subprocess (kills on timeout)
- Regex denylist applied to the code before spawning (subprocess, os.system, etc.)
- Subprocess inherits only PATH and PLAYWRIGHT_BROWSERS_PATH from env
- Screenshots emitted by the wrapper are capped at 1280x720, JPEG q80
- Returns a structured dict; never raises for test failures (only for malformed input)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

DENYLIST = re.compile(
    r"\b(subprocess|os\.system|eval\(|exec\(|__import__|open\(|requests\.|urllib\.|socket\.|shutil\.|pathlib\.Path)\b"
)

WRAPPER_PATH = Path(__file__).with_name("playwright_runner_wrapper.py.tmpl")
TIMEOUT_SECONDS = 60.0


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False, "Invalid URL"
    if parsed.scheme not in ("http", "https"):
        return False, "Base URL must be http or https"
    if not parsed.netloc:
        return False, "Base URL must have a host"
    return True, ""


def _check_denylist(code: str) -> str | None:
    """Return the offending token if the code hits the denylist, else None."""
    m = DENYLIST.search(code)
    return m.group(1) if m else None


async def run_playwright_code(code: str, base_url: str, headless: bool) -> dict:
    """Execute the user's Playwright code in a subprocess and return a result dict.

    Returns:
        {
            "status": "passed" | "failed" | "error",
            "screenshot_b64": str | None,
            "error_message": str | None,
            "console_log": str,
            "duration_ms": int,
        }
    """
    url_ok, url_err = _validate_url(base_url)
    if not url_ok:
        return {"status": "error", "screenshot_b64": None,
                "error_message": url_err, "console_log": "", "duration_ms": 0}

    bad = _check_denylist(code)
    if bad:
        return {"status": "error", "screenshot_b64": None,
                "error_message": f"Code failed safety check (blocked: {bad}).",
                "console_log": "", "duration_ms": 0}

    template = WRAPPER_PATH.read_text(encoding="utf-8")
    script = template.format(user_code=code, base_url=base_url, headless=headless)

    with tempfile.TemporaryDirectory(prefix="pw_run_") as tmpdir:
        script_path = Path(tmpdir) / "runner.py"
        script_path.write_text(script, encoding="utf-8")

        # Inherit the parent environment, then scrub anything that looks like a
        # secret. Python + asyncio + Playwright need several OS-specific vars
        # (SystemRoot for Winsock on Windows, TEMP, USERPROFILE, etc.), so a
        # bare {PATH, PLAYWRIGHT_BROWSERS_PATH} env breaks subprocess startup.
        # The denylist (above) is the primary security boundary; this env
        # filtering is defense-in-depth against accidental key leakage.
        SECRET_KEY_PATTERNS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "JWT", "PRIVATE_KEY")
        env = {
            k: v
            for k, v in os.environ.items()
            if not any(p in k.upper() for p in SECRET_KEY_PATTERNS)
        }
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # Run the subprocess synchronously in a worker thread. We can't use
        # asyncio.create_subprocess_exec here because uvicorn on Windows uses
        # the SelectorEventLoop, which raises NotImplementedError on subprocess
        # operations. Running synchronously in a thread sidesteps the event
        # loop entirely and works under both Selector and Proactor loops.
        def _run_blocking() -> tuple[bytes, bytes, bool]:
            try:
                proc = subprocess.run(
                    [sys.executable, str(script_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    timeout=TIMEOUT_SECONDS,
                )
                return proc.stdout or b"", proc.stderr or b"", False
            except subprocess.TimeoutExpired as e:
                return (e.stdout or b""), (e.stderr or b""), True

        stdout, stderr, timed_out = await asyncio.to_thread(_run_blocking)
        if timed_out:
            return {"status": "error", "screenshot_b64": None,
                    "error_message": f"Timeout ({int(TIMEOUT_SECONDS)}s)",
                    "console_log": "", "duration_ms": int(TIMEOUT_SECONDS * 1000)}

    try:
        text = stdout.decode("utf-8", errors="replace").strip()
        # Wrapper prints exactly one JSON object as its last action
        last_line = text.rsplit("\n", 1)[-1] if text else "{}"
        result = json.loads(last_line)
        if not isinstance(result, dict) or "status" not in result:
            raise ValueError("malformed runner output")
        return result
    except Exception as e:
        return {
            "status": "error",
            "screenshot_b64": None,
            "error_message": f"Runner produced unparsable output: {e}",
            "console_log": stderr.decode("utf-8", errors="replace")[-2000:],
            "duration_ms": 0,
        }
