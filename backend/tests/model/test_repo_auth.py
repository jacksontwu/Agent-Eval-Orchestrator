from app.core.permissions import PermissionCode
from app.core.security import hash_password
from app.model import repo_auth


def test_bootstrap_creates_builtin_groups_and_permissions(session):
    repo_auth.bootstrap_rbac(session)

    groups = {group.name: group for group in repo_auth.list_groups(session)}
    permissions = {permission.code for permission in repo_auth.list_permissions(session)}

    assert groups["admin"].is_builtin is True
    assert groups["user"].is_builtin is True
    assert groups["bot"].is_builtin is True
    assert PermissionCode.USERS_MANAGE in permissions
    assert PermissionCode.GROUPS_MANAGE in permissions
    assert PermissionCode.WORKER_PROTOCOL_USE in permissions
    assert repo_auth.permissions_for_group(session, "admin") == set(PermissionCode.all())
    assert PermissionCode.WORKER_PROTOCOL_USE in repo_auth.permissions_for_group(session, "bot")


def test_create_user_and_resolve_permissions(session):
    repo_auth.bootstrap_rbac(session)
    user = repo_auth.create_user(
        session,
        username="alice",
        display_name="Alice",
        password_hash=hash_password("secret"),
        group_names=["user"],
    )
    session.commit()

    loaded = repo_auth.get_user_by_username(session, "alice")
    assert loaded is not None
    assert loaded.user_id == user.user_id
    assert repo_auth.group_names_for_user(session, user.user_id) == ["user"]
    assert repo_auth.permissions_for_user(session, user.user_id) == {
        PermissionCode.TASKS_CREATE,
        PermissionCode.TASKS_READ_OWN,
        PermissionCode.TASKS_MANAGE_OWN,
        PermissionCode.WORKERS_READ,
    }


def test_custom_group_permission_assignment(session):
    repo_auth.bootstrap_rbac(session)
    group = repo_auth.create_group(
        session,
        name="reviewer",
        display_name="Reviewer",
        description="Can review all task results",
    )
    repo_auth.set_group_permissions(
        session,
        group.group_id,
        [PermissionCode.TASKS_READ_ALL, PermissionCode.WORKERS_READ],
    )
    session.commit()

    assert repo_auth.permissions_for_group(session, "reviewer") == {
        PermissionCode.TASKS_READ_ALL,
        PermissionCode.WORKERS_READ,
    }
