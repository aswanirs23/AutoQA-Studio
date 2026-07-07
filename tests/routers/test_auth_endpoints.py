"""Integration tests for PUT /api/projects/{id}/auth and POST .../auth/verify.

Uses the `client` fixture from tests/routers/conftest.py, which points the app at
an isolated temp SQLite DB (never the real data/testgen.db) and runs the app's
lifespan (init_db) via TestClient's context manager.
"""

from __future__ import annotations


def _new_project(client):
    r = client.post("/api/projects", json={"name": "P", "description": ""})
    return r.json()["id"]


def test_put_auth_masks_password_and_get_is_masked(client):
    pid = _new_project(client)
    r = client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login", "username": "bob", "password": "s3cret"})
    assert r.status_code == 200
    body = r.json()["auth_config"]
    assert "password" not in body and body["password_set"] is True
    assert body["username"] == "bob"
    # GET project also masked
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert "password" not in got and got["password_set"] is True


def test_put_auth_without_password_keeps_existing(client):
    pid = _new_project(client)
    client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login", "username": "bob", "password": "s3cret"})
    # second save omits password -> keep the stored one
    client.put(f"/api/projects/{pid}/auth", json={
        "login_url": "http://x/login2", "username": "bob"})
    got = client.get(f"/api/projects/{pid}").json()["project"]["auth_config"]
    assert got["password_set"] is True
    assert got["login_url"] == "http://x/login2"
