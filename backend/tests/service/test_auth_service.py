import pytest

from app.core.config import get_settings
from app.core.permissions import PermissionCode
from app.core.security import hash_password
from app.model import repo_auth
from app.service import auth_service


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_config_admin_login_without_db(monkeypatch):
    monkeypatch.setenv("AEO_ADMIN_USERNAME", "root")
    monkeypatch.setenv("AEO_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret-with-at-least-32-bytes")

    principal = auth_service.authenticate_config_user("root", "secret")

    assert principal is not None
    assert principal.username == "root"
    assert principal.source == "config"
    assert "admin" in principal.groups
    assert PermissionCode.USERS_MANAGE in principal.permissions
    assert PermissionCode.GROUPS_MANAGE in principal.permissions


def test_config_bot_login(monkeypatch):
    monkeypatch.setenv("AEO_BOT_USERNAME", "worker-bot")
    monkeypatch.setenv("AEO_BOT_PASSWORD", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret-with-at-least-32-bytes")

    principal = auth_service.authenticate_config_user("worker-bot", "secret")

    assert principal is not None
    assert principal.username == "worker-bot"
    assert principal.groups == ["bot"]
    assert principal.permissions == [PermissionCode.WORKER_PROTOCOL_USE, PermissionCode.ASSETS_USE]


def test_db_user_login(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret-with-at-least-32-bytes")
    repo_auth.bootstrap_rbac(session)
    repo_auth.create_user(
        session,
        username="alice",
        display_name="Alice",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    session.commit()

    principal = auth_service.authenticate_db_user(session, "alice", "secret")

    assert principal is not None
    assert principal.username == "alice"
    assert principal.source == "db"
    assert principal.groups == ["user"]
    assert PermissionCode.TASKS_CREATE in principal.permissions


def test_disabled_db_user_cannot_login(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret-with-at-least-32-bytes")
    repo_auth.bootstrap_rbac(session)
    user = repo_auth.create_user(
        session,
        username="alice",
        display_name="Alice",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    user.is_active = False
    session.commit()

    principal = auth_service.authenticate_db_user(session, "alice", "secret")

    assert principal is None
