from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth


UNIT_SECRET = "unit-secret-with-at-least-32-bytes"


def test_login_config_admin(monkeypatch, session):
    monkeypatch.setenv("AEO_ADMIN_USERNAME", "root")
    monkeypatch.setenv("AEO_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.post("/api/auth/login", json={"username": "root", "password": "secret"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tokenType"] == "bearer"
    assert body["accessToken"]
    assert body["user"]["username"] == "root"
    assert body["user"]["source"] == "config"


def test_login_db_user_and_me(monkeypatch, session):
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="alice",
        display_name="Alice",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    session.commit()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    token = login.json()["accessToken"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200, me.text
    assert me.json()["username"] == "alice"
    assert me.json()["groups"] == ["user"]


def test_login_rejects_bad_password(monkeypatch, session):
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})

    assert resp.status_code == 401


def test_protected_route_requires_bearer_token(monkeypatch, session):
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.get("/api/task-templates")

    assert resp.status_code == 401


def test_old_shared_token_header_no_longer_authorizes(monkeypatch, session):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", UNIT_SECRET)
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.get("/api/task-templates", headers={"X-AEO-Token": "secret"})

    assert resp.status_code == 401
