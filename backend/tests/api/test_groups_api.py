from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.permissions import PermissionCode
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


def _admin_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def _seed_admin(session):
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="admin",
        display_name="Admin",
        password_hash=hash_password("secret"),
        group_names=["admin"],
    )
    session.commit()


def test_admin_creates_group_and_sets_permissions(session, monkeypatch):
    _seed_admin(session)
    client = _client(session, monkeypatch)
    headers = _admin_headers(client)

    create = client.post(
        "/api/groups",
        headers=headers,
        json={"name": "reviewer", "displayName": "Reviewer", "description": "Reviews all results"},
    )
    assert create.status_code == 201, create.text
    group_id = create.json()["groupId"]
    assert create.json()["isBuiltin"] is False

    update = client.put(
        f"/api/groups/{group_id}/permissions",
        headers=headers,
        json={"permissions": [PermissionCode.TASKS_READ_ALL, PermissionCode.WORKERS_READ]},
    )
    assert update.status_code == 200, update.text
    assert set(update.json()["permissions"]) == {PermissionCode.TASKS_READ_ALL, PermissionCode.WORKERS_READ}


def test_builtin_group_protection(session, monkeypatch):
    _seed_admin(session)
    client = _client(session, monkeypatch)
    headers = _admin_headers(client)
    groups = client.get("/api/groups", headers=headers).json()["groups"]
    admin = next(group for group in groups if group["name"] == "admin")

    delete = client.delete(f"/api/groups/{admin['groupId']}", headers=headers)
    assert delete.status_code == 400

    remove_required = client.put(
        f"/api/groups/{admin['groupId']}/permissions",
        headers=headers,
        json={"permissions": [PermissionCode.USERS_MANAGE]},
    )
    assert remove_required.status_code == 400


def test_permissions_endpoint_returns_builtin_codes(session, monkeypatch):
    _seed_admin(session)
    client = _client(session, monkeypatch)
    headers = _admin_headers(client)

    resp = client.get("/api/permissions", headers=headers)

    assert resp.status_code == 200
    codes = {item["code"] for item in resp.json()["permissions"]}
    assert PermissionCode.GROUPS_MANAGE in codes
    assert PermissionCode.WORKER_PROTOCOL_USE in codes
