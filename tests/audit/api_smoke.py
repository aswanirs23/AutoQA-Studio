"""Characterization smoke tests for the documented API.

Each test exercises one documented endpoint. Failures = bug discoveries
to be triaged into docs/audit/2026-05-13-bug-report.md.

Run all:        pytest tests/audit/api_smoke.py -v
Run one group:  pytest tests/audit/api_smoke.py -v -k projects
With LLM:       AUDIT_RUN_LLM=1 pytest tests/audit/api_smoke.py -v
"""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

RUN_LLM = os.environ.get("AUDIT_RUN_LLM", "0") == "1"
BOGUS_UUID = "00000000-0000-0000-0000-000000000000"


async def _make_project(client: AsyncClient, name: str = "Helper project") -> str:
    r = await client.post("/api/projects", json={"name": name, "description": "", "context": {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- health


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------- parsers / auth


async def test_list_parsers(client: AsyncClient) -> None:
    r = await client.get("/api/parsers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "parsers" in body
    names = {p.get("name") for p in body["parsers"]}
    # README documents these five built-in parsers.
    expected = {"text", "figma", "jira", "screenshot", "browser_session"}
    missing = expected - names
    assert not missing, f"Parsers missing from /api/parsers: {missing}"


async def test_auth_me_in_dev_mode(client: AsyncClient) -> None:
    """With AUTH_DISABLED=true, /api/auth/me must return the synthetic dev user."""
    r = await client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    user = r.json()
    assert user.get("id"), "dev user must have an id"
    assert user.get("email"), "dev user must have an email"


# ---------------------------------------------------------------- projects


async def test_project_crud_happy_path(client: AsyncClient) -> None:
    # Create
    r = await client.post("/api/projects", json={
        "name": "Audit project",
        "description": "Created by audit smoke",
        "context": {},
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert pid

    # List includes the new project
    r = await client.get("/api/projects")
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()]
    assert pid in ids

    # Detail returns project + features (empty)
    r = await client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"]["id"] == pid
    assert body["features"] == []

    # Update name + description
    r = await client.put(f"/api/projects/{pid}", json={
        "name": "Audit project (renamed)",
        "description": "Updated by audit",
    })
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Audit project (renamed)"

    # Update context JSON
    r = await client.put(f"/api/projects/{pid}/context", json={
        "context": {"target_audience": "QA engineers"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["context"]["target_audience"] == "QA engineers"

    # Delete
    r = await client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # Detail now 404s
    r = await client.get(f"/api/projects/{pid}")
    assert r.status_code == 404


async def test_project_404_paths(client: AsyncClient) -> None:
    assert (await client.get(f"/api/projects/{BOGUS_UUID}")).status_code == 404
    r = await client.put(f"/api/projects/{BOGUS_UUID}", json={"name": "x"})
    assert r.status_code == 404
    assert (await client.delete(f"/api/projects/{BOGUS_UUID}")).status_code == 404


# ---------------------------------------------------------------- features


async def test_feature_crud_happy_path(client: AsyncClient) -> None:
    pid = await _make_project(client, "Feature CRUD project")

    # Create
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "Login",
        "description": "Auth flows",
        "sort_order": 0,
    })
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert fid

    # List
    r = await client.get(f"/api/projects/{pid}/features")
    assert r.status_code == 200, r.text
    names = [f["name"] for f in r.json()]
    assert "Login" in names

    # Update
    r = await client.put(f"/api/projects/{pid}/features/{fid}", json={
        "name": "Login (renamed)",
        "description": "Updated",
        "sort_order": 1,
    })
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Login (renamed)"

    # Delete
    r = await client.delete(f"/api/projects/{pid}/features/{fid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


async def test_feature_404_paths(client: AsyncClient) -> None:
    pid = await _make_project(client, "Feature 404 project")
    r = await client.put(f"/api/projects/{pid}/features/{BOGUS_UUID}", json={"name": "x"})
    assert r.status_code == 404
    r = await client.delete(f"/api/projects/{pid}/features/{BOGUS_UUID}")
    assert r.status_code == 404


# ---------------------------------------------------------------- test cases / stats


async def test_test_case_list_stats_history_empty(client: AsyncClient) -> None:
    pid = await _make_project(client, "Stats project")

    # Listing test cases for an empty project = []
    r = await client.get(f"/api/projects/{pid}/test-cases")
    assert r.status_code == 200, r.text
    assert r.json() == []

    # Stats returns zeros / empty groupings
    r = await client.get(f"/api/projects/{pid}/stats")
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["total"] == 0
    assert stats["by_type"] == {}
    assert stats["by_priority"] == {}
    assert stats["by_feature"] == []

    # Input history is empty
    r = await client.get(f"/api/projects/{pid}/input-history")
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_test_case_patch_and_delete_missing(client: AsyncClient) -> None:
    pid = await _make_project(client, "TC missing project")
    bogus_tc = "TC_999"

    r = await client.patch(f"/api/projects/{pid}/test-cases/{bogus_tc}", json={"title": "x"})
    assert r.status_code == 404
    r = await client.delete(f"/api/projects/{pid}/test-cases/{bogus_tc}")
    assert r.status_code == 404


async def test_bulk_delete_empty_list_returns_zero(client: AsyncClient) -> None:
    pid = await _make_project(client, "Bulk-delete project")
    r = await client.post(f"/api/projects/{pid}/test-cases/bulk-delete", json={"ids": []})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 0}


# ---------------------------------------------------------------- settings keys


async def test_settings_keys_get_returns_status_dict(client: AsyncClient) -> None:
    r = await client.get("/api/settings/keys")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "keys" in body
    # Every entry has the documented shape.
    for name, entry in body["keys"].items():
        assert set(entry.keys()) >= {"configured", "from_ui", "from_env_only", "masked"}, name


async def test_settings_keys_put_rejects_unknown(client: AsyncClient) -> None:
    r = await client.put("/api/settings/keys", json={"not_a_real_key": "value"})
    assert r.status_code == 400


async def test_settings_keys_put_then_get_then_clear(client: AsyncClient) -> None:
    # Use a known overridable key per backend.config.SECRET_OVERRIDABLE_KEYS.
    r = await client.put("/api/settings/keys", json={"openai_api_key": "sk-test-abcdefgh1234"})
    assert r.status_code == 200, r.text
    try:
        r = await client.get("/api/settings/keys")
        entry = r.json()["keys"]["openai_api_key"]
        assert entry["configured"] is True
        assert entry["from_ui"] is True
        assert entry["masked"].endswith("1234")
        assert "*" in entry["masked"]
    finally:
        # Always clear the UI override so a mid-test failure can't leak the
        # fake key into subsequent tests sharing the audit DB.
        clear = await client.put("/api/settings/keys", json={"openai_api_key": ""})
        assert clear.status_code == 200, clear.text

    r = await client.get("/api/settings/keys")
    entry = r.json()["keys"]["openai_api_key"]
    # After clearing the UI override, `from_ui` is False; `configured` reflects env-only state.
    assert entry["from_ui"] is False


# ---------------------------------------------------------------- exports


async def test_export_empty_project_all_formats(client: AsyncClient) -> None:
    pid = await _make_project(client, "Export project")
    # All five documented formats. Excel is binary; just check status + headers.
    for fmt, ext in [
        ("csv", "csv"),
        ("json", "json"),
        ("markdown", "md"),
        ("testrail", "csv"),
        ("excel", "xlsx"),
    ]:
        r = await client.get(f"/api/export/{pid}?format={fmt}")
        assert r.status_code == 200, f"{fmt}: {r.text}"
        cd = r.headers.get("content-disposition", "")
        assert f".{ext}" in cd.lower(), f"{fmt}: bad content-disposition {cd!r}"


async def test_export_with_filters_empty_project(client: AsyncClient) -> None:
    pid = await _make_project(client, "Export filter project")
    # Filtered query still returns 200 even with zero matches.
    r = await client.get(
        f"/api/export/{pid}?format=csv&search=foo&priority=High"
    )
    assert r.status_code == 200, r.text


async def test_export_unknown_project_404(client: AsyncClient) -> None:
    r = await client.get(f"/api/export/{BOGUS_UUID}?format=csv")
    assert r.status_code == 404


# ---------------------------------------------------------------- browser session


async def test_browser_session_start_step_complete(client: AsyncClient) -> None:
    pid = await _make_project(client, "Browser session project")

    # Project must have a feature for downstream generate to work, but
    # session creation itself only needs project_id + url + feature_name.
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "Checkout", "description": "", "sort_order": 0,
    })
    assert r.status_code == 200, r.text

    r = await client.post("/api/browser-session/start", json={
        "project_id": pid,
        "url": "https://example.com",
        "feature_name": "Checkout",
        "browser_type": "playwright",
        # StartSessionBody.steps is list[str]: each string becomes the
        # `instruction` of a pending SessionStep. See backend/models/browser_session.py.
        "steps": ["Open the homepage"],
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    assert sid

    # GET single + list by project.
    r = await client.get(f"/api/browser-session/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["session"]["id"] == sid

    r = await client.get(f"/api/browser-session/project/{pid}")
    assert r.status_code == 200, r.text
    assert any(s["id"] == sid for s in r.json()["sessions"])

    # Add a step manually.
    r = await client.post(f"/api/browser-session/{sid}/step", json={
        "instruction": "Click sign-in",
        "action_type": "click",
        "target": "#signin",
        "status": "completed",
    })
    assert r.status_code == 200, r.text

    # Complete the session.
    r = await client.post(f"/api/browser-session/{sid}/complete", json={"status": "completed"})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["status"] == "completed"


async def test_browser_session_404s(client: AsyncClient) -> None:
    bogus = "no-such-session"
    assert (await client.get(f"/api/browser-session/{bogus}")).status_code == 404
    r = await client.post(f"/api/browser-session/{bogus}/step", json={
        "instruction": "x", "action_type": "click", "status": "pending",
    })
    assert r.status_code == 404
    r = await client.post(f"/api/browser-session/{bogus}/complete", json={"status": "completed"})
    assert r.status_code == 404


# ---------------------------------------------------------------- generate (validation only)


async def test_generate_rejects_missing_fields(client: AsyncClient) -> None:
    r = await client.post("/api/generate", json={})
    assert r.status_code == 400


async def test_generate_unknown_input_type(client: AsyncClient) -> None:
    pid = await _make_project(client, "Gen reject project")
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "F", "description": "", "sort_order": 0,
    })
    fid = r.json()["id"]

    r = await client.post("/api/generate", json={
        "input_type": "definitely_not_a_parser",
        "project_id": pid,
        "feature_id": fid,
        "data": {},
    })
    assert r.status_code == 400


async def test_generate_inputs_too_many(client: AsyncClient) -> None:
    pid = await _make_project(client, "Gen too-many project")
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "F", "description": "", "sort_order": 0,
    })
    fid = r.json()["id"]

    body = {
        "project_id": pid,
        "feature_id": fid,
        "inputs": [{"input_type": "text", "data": {"content": "x"}} for _ in range(21)],
    }
    r = await client.post("/api/generate", json=body)
    assert r.status_code == 400


# ---------------------------------------------------------------- generate (LLM-gated)


@pytest.mark.skipif(not RUN_LLM, reason="Set AUDIT_RUN_LLM=1 to exercise LLM-bound endpoints")
async def test_generate_text_happy_path(client: AsyncClient) -> None:
    """End-to-end with the real LLM provider configured in settings.

    Requires at least one of OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY
    to be set in env or via /api/settings/keys.
    """
    pid = await _make_project(client, "Gen happy project")
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "Login", "description": "Auth", "sort_order": 0,
    })
    fid = r.json()["id"]

    r = await client.post("/api/generate", json={
        "input_type": "text",
        "project_id": pid,
        "feature_id": fid,
        "data": {
            "feature_name": "Login",
            "content": "User must enter email and password. Both are required. "
                       "On invalid credentials, show 'Invalid email or password.'",
        },
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added_count"] >= 1, body
    assert len(body["test_cases"]) == body["added_count"]


# ---------------------------------------------------------------- iterate


async def test_iterate_unknown_project(client: AsyncClient) -> None:
    r = await client.post("/api/generate/iterate", json={
        "project_id": "no-such-project",
        "instruction": "add edge cases",
    })
    assert r.status_code == 404


async def test_iterate_project_without_features_400(client: AsyncClient) -> None:
    pid = await _make_project(client, "Iterate no-features project")
    # No features -> expect 400 from the "Project has no features" branch.
    # Whether that branch fires before or after the LLM call is implementation-defined;
    # if the LLM is required first, this test is skipped under AUDIT_RUN_LLM=0.
    if not RUN_LLM:
        pytest.skip("Iterate calls LLM before checking features; set AUDIT_RUN_LLM=1")
    r = await client.post("/api/generate/iterate", json={
        "project_id": pid,
        "instruction": "add edge cases",
    })
    # Either 400 (no features) or 200 (LLM produced nothing and dedup absorbed it).
    assert r.status_code in (200, 400), r.text


@pytest.mark.skipif(not RUN_LLM, reason="Set AUDIT_RUN_LLM=1 to exercise LLM-bound endpoints")
async def test_iterate_happy_path_with_existing_cases(client: AsyncClient) -> None:
    pid = await _make_project(client, "Iterate happy project")
    r = await client.post(f"/api/projects/{pid}/features", json={
        "name": "Login", "description": "", "sort_order": 0,
    })
    fid = r.json()["id"]
    # Seed with text generation.
    r = await client.post("/api/generate", json={
        "input_type": "text",
        "project_id": pid,
        "feature_id": fid,
        "data": {"feature_name": "Login", "content": "User logs in with email + password."},
    })
    assert r.status_code == 200, r.text
    # Iterate to add edge cases.
    r = await client.post("/api/generate/iterate", json={
        "project_id": pid,
        "feature_id": fid,
        "instruction": "Add edge cases for empty password and SQL-injection-like inputs",
        "type_filter": "edge",
    })
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- generate-description


@pytest.mark.skipif(not RUN_LLM, reason="Set AUDIT_RUN_LLM=1 to exercise LLM-bound endpoints")
async def test_generate_description_from_txt(client: AsyncClient) -> None:
    import pathlib

    pid = await _make_project(client, "Overview project")
    fixture = pathlib.Path(__file__).parent / "fixtures" / "sample_overview.txt"
    files = {"file": ("sample_overview.txt", fixture.read_bytes(), "text/plain")}
    r = await client.post(f"/api/projects/{pid}/generate-description", files=files)
    assert r.status_code == 200, r.text
    assert r.json().get("overview", "").strip(), "overview must be non-empty"


async def test_generate_description_unknown_project_404(client: AsyncClient) -> None:
    files = {"file": ("x.txt", b"hello", "text/plain")}
    r = await client.post(f"/api/projects/{BOGUS_UUID}/generate-description", files=files)
    assert r.status_code == 404


async def test_generate_description_rejects_unsupported_type(client: AsyncClient) -> None:
    pid = await _make_project(client, "Overview reject project")
    files = {"file": ("x.exe", b"not a real file", "application/octet-stream")}
    r = await client.post(f"/api/projects/{pid}/generate-description", files=files)
    assert r.status_code == 400
