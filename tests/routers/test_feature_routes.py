"""Integration tests for /api/features/* and /api/generation-inputs/* routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.main import app
    import backend.deps as deps

    async def fake_user_id():
        return "test-user"

    app.dependency_overrides[deps.get_current_user_id] = fake_user_id
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_generations_for_unknown_feature_returns_404(client):
    r = client.get("/api/features/does-not-exist/generations")
    assert r.status_code == 404


def test_get_image_for_unknown_input_returns_404(client):
    r = client.get("/api/generation-inputs/does-not-exist/image")
    assert r.status_code == 404
