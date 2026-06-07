from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth


UNIT_SECRET = "unit-secret-with-at-least-32-bytes"


def _client(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def _login(client: TestClient, username: str, password: str = "secret") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def test_admin_creates_lists_and_disables_user(session, monkeypatch):
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="admin",
        display_name="Admin",
        password_hash=hash_password("secret"),
        group_names=["admin"],
    )
    session.commit()
    client = _client(session, monkeypatch)
    headers = _login(client, "admin")

    create = client.post(
        "/api/users",
        headers=headers,
        json={
            "username": "alice",
            "displayName": "Alice",
            "password": "alice-secret",
            "groups": ["user"],
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["username"] == "alice"
    assert body["groups"] == ["user"]
    assert "passwordHash" not in body

    listed = client.get("/api/users", headers=headers)
    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()["users"]} == {"admin", "alice"}

    delete = client.delete(f"/api/users/{body['userId']}", headers=headers)
    assert delete.status_code == 200
    assert delete.json() == {"ok": True}

    disabled_login = client.post("/api/auth/login", json={"username": "alice", "password": "alice-secret"})
    assert disabled_login.status_code == 401


def test_non_admin_cannot_manage_users(session, monkeypatch):
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="bob",
        display_name="Bob",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    session.commit()
    client = _client(session, monkeypatch)
    headers = _login(client, "bob")

    resp = client.get("/api/users", headers=headers)

    assert resp.status_code == 403


def test_config_users_not_listed(session, monkeypatch):
    monkeypatch.setenv("AEO_ADMIN_USERNAME", "root")
    monkeypatch.setenv("AEO_ADMIN_PASSWORD", "secret")
    repo_auth.bootstrap_rbac(session)
    session.commit()
    client = _client(session, monkeypatch)
    headers = _login(client, "root")

    resp = client.get("/api/users", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["users"] == []
