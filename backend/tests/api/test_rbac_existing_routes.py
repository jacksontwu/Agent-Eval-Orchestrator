from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth, repo_runs


UNIT_SECRET = "unit-secret-with-at-least-32-bytes"


def _client(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def _login(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": "secret"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def _seed_users(session):
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="admin",
        display_name="Admin",
        password_hash=hash_password("secret"),
        group_names=["admin"],
    )
    repo_auth.create_user(
        session,
        username="alice",
        display_name="Alice",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    repo_auth.create_user(
        session,
        username="bob",
        display_name="Bob",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    session.commit()


def test_user_cannot_read_other_users_run(session, monkeypatch):
    _seed_users(session)
    run = repo_runs.create_run(session, template_id="tpl-1", owner="bob", display_name="Bob Run")
    session.commit()
    client = _client(session, monkeypatch)
    headers = _login(client, "alice")

    resp = client.get(f"/api/eval-tasks/{run.run_id}", headers=headers)

    assert resp.status_code == 403


def test_admin_can_read_other_users_run(session, monkeypatch):
    _seed_users(session)
    run = repo_runs.create_run(session, template_id="tpl-1", owner="bob", display_name="Bob Run")
    session.commit()
    client = _client(session, monkeypatch)
    headers = _login(client, "admin")

    resp = client.get(f"/api/eval-tasks/{run.run_id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"] == "bob"


def test_worker_manage_requires_admin(session, monkeypatch):
    _seed_users(session)
    client = _client(session, monkeypatch)
    headers = _login(client, "alice")

    resp = client.post("/api/workers/worker-1/settings", headers=headers, json={"enabled": False})

    assert resp.status_code == 403


def test_worker_protocol_requires_bot_or_admin(session, monkeypatch):
    _seed_users(session)
    client = _client(session, monkeypatch)
    headers = _login(client, "alice")

    resp = client.post(
        "/api/workers/register",
        headers=headers,
        json={"workerId": "w1", "displayName": "w1", "host": "h", "slotsTotal": 1, "capabilities": {}},
    )

    assert resp.status_code == 403
