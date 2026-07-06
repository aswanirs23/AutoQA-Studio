"""Tests for the 'Mark as Expected Behavior' adapt flow.

Two always-run tests cover the API's 404 paths. One LLM-gated test exercises
the real LLM rewrite.

Run with:
    pytest tests/audit/test_adapt_expected.py -v
    AUDIT_RUN_LLM=1 pytest tests/audit/test_adapt_expected.py -v
"""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

RUN_LLM = os.environ.get("AUDIT_RUN_LLM", "0") == "1"


async def test_suggest_expected_unknown_project(client: AsyncClient) -> None:
    """Bogus project id → 404."""
    r = await client.post(
        "/api/projects/no-such-project/test-cases/TC_999/suggest-expected-result",
        json={
            "actual_page_text": "Login error: Bad credentials",
            "current_expected_result": "Invalid credentials message displayed",
            "error_message": "Assertion failed",
        },
    )
    assert r.status_code == 404, r.text
    assert "Project not found" in r.json().get("detail", "")


async def test_suggest_expected_unknown_test_case(client: AsyncClient) -> None:
    """Existing project but unknown TC id → 404."""
    r = await client.post("/api/projects", json={"name": "Adapt 404 project", "description": "", "context": {}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    r = await client.post(
        f"/api/projects/{pid}/test-cases/TC_DOES_NOT_EXIST/suggest-expected-result",
        json={
            "actual_page_text": "Bad credentials",
            "current_expected_result": "Bad credentials",
            "error_message": "",
        },
    )
    assert r.status_code == 404, r.text
    assert "Test case not found" in r.json().get("detail", "")


@pytest.mark.skipif(not RUN_LLM, reason="Set AUDIT_RUN_LLM=1 to exercise LLM-bound endpoints")
async def test_suggest_expected_happy_path(client: AsyncClient) -> None:
    """LLM rewrites a divergent expected_result to match observed page text."""
    r = await client.post("/api/projects", json={"name": "Adapt happy project", "description": "", "context": {}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = await client.post(f"/api/projects/{pid}/features", json={"name": "Login", "description": "", "sort_order": 0})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    r = await client.post("/api/generate", json={
        "input_type": "text",
        "project_id": pid,
        "feature_id": fid,
        "data": {
            "feature_name": "Login",
            "content": (
                "Users sign in with email and password. On invalid credentials, an error "
                "message 'Invalid email or password' is displayed."
            ),
        },
    })
    assert r.status_code == 200, r.text
    cases = r.json()["test_cases"]
    assert cases, "Expected at least one test case from text generation"
    tcid = cases[0]["id"]

    r = await client.post(
        f"/api/projects/{pid}/test-cases/{tcid}/suggest-expected-result",
        json={
            "actual_page_text": (
                "Epic sadface: Username and password do not match any user in this service"
            ),
            "current_expected_result": "An error message 'Invalid email or password' is displayed",
            "error_message": "Assertion failed",
        },
    )
    assert r.status_code == 200, r.text
    suggested = r.json()["suggested"]
    assert suggested, "Expected a non-empty suggested rewrite"
    lo = suggested.lower()
    assert any(tok in lo for tok in ("username", "password", "credentials", "match")), (
        f"Suggestion seems off-topic: {suggested!r}"
    )
