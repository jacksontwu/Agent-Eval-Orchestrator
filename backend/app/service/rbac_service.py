from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.core.permissions import PermissionCode
from app.core.security import hash_password
from app.model import repo_auth
from app.model.tables import Group, User
from app.service.errors import ConflictError, NotFoundError, ServiceError


def user_to_read(session: Session, user: User) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "is_active": bool(user.is_active),
        "groups": repo_auth.group_names_for_user(session, user.user_id),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login_at": user.last_login_at,
    }


def list_users(session: Session) -> list[dict]:
    return [user_to_read(session, user) for user in repo_auth.list_users(session)]


def create_user(session: Session, *, username: str, display_name: str, password: str, groups: list[str]) -> dict:
    if repo_auth.get_user_by_username(session, username) is not None:
        raise ConflictError(f"user already exists: {username}")
    user = repo_auth.create_user(
        session,
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        group_names=groups,
    )
    session.commit()
    return user_to_read(session, user)


def get_user_read(session: Session, user_id: str) -> dict:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    return user_to_read(session, user)


def update_user(
    session: Session,
    user_id: str,
    *,
    display_name: str | None,
    is_active: bool | None,
    groups: list[str] | None,
) -> dict:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    if display_name is not None:
        user.display_name = display_name
    if is_active is not None:
        user.is_active = is_active
    if groups is not None:
        repo_auth.set_user_groups(session, user.user_id, groups)
    user.updated_at = now_iso()
    session.commit()
    return user_to_read(session, user)


def disable_user(session: Session, user_id: str) -> None:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    user.is_active = False
    user.updated_at = now_iso()
    session.commit()


def reset_password(session: Session, user_id: str, password: str) -> None:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    user.password_hash = hash_password(password)
    user.updated_at = now_iso()
    session.commit()


def group_to_read(session: Session, group: Group) -> dict:
    return {
        "group_id": group.group_id,
        "name": group.name,
        "display_name": group.display_name,
        "description": group.description,
        "is_builtin": bool(group.is_builtin),
        "is_active": bool(group.is_active),
        "permissions": sorted(repo_auth.permissions_for_group(session, group.name)),
    }


def list_groups(session: Session) -> list[dict]:
    return [group_to_read(session, group) for group in repo_auth.list_groups(session)]


def list_permissions(session: Session) -> list[dict]:
    return [{"code": item.code, "description": item.description} for item in repo_auth.list_permissions(session)]


def create_group(session: Session, *, name: str, display_name: str, description: str) -> dict:
    if repo_auth.get_group_by_name(session, name) is not None:
        raise ConflictError(f"group already exists: {name}")
    group = repo_auth.create_group(session, name=name, display_name=display_name, description=description)
    session.commit()
    return group_to_read(session, group)


def _require_group(session: Session, group_id: str) -> Group:
    group = repo_auth.get_group(session, group_id)
    if group is None:
        raise NotFoundError(f"group not found: {group_id}")
    return group


def get_group_read(session: Session, group_id: str) -> dict:
    return group_to_read(session, _require_group(session, group_id))


def update_group(
    session: Session,
    group_id: str,
    *,
    display_name: str | None,
    description: str | None,
    is_active: bool | None,
) -> dict:
    group = _require_group(session, group_id)
    if group.is_builtin and is_active is False:
        raise ServiceError(f"builtin group cannot be disabled: {group.name}")
    if display_name is not None:
        group.display_name = display_name
    if description is not None:
        group.description = description
    if is_active is not None:
        group.is_active = is_active
    group.updated_at = now_iso()
    session.commit()
    return group_to_read(session, group)


def disable_group(session: Session, group_id: str) -> None:
    group = _require_group(session, group_id)
    if group.is_builtin:
        raise ServiceError(f"builtin group cannot be deleted: {group.name}")
    group.is_active = False
    group.updated_at = now_iso()
    session.commit()


def set_group_permissions(session: Session, group_id: str, permissions: list[str]) -> dict:
    group = _require_group(session, group_id)
    permission_set = set(permissions)
    if group.name == "admin" and not {PermissionCode.USERS_MANAGE, PermissionCode.GROUPS_MANAGE}.issubset(
        permission_set
    ):
        raise ServiceError("admin group must keep users.manage and groups.manage")
    if group.name == "bot" and not {PermissionCode.WORKER_PROTOCOL_USE, PermissionCode.ASSETS_USE}.issubset(
        permission_set
    ):
        raise ServiceError("bot group must keep worker_protocol.use and assets.use")
    repo_auth.set_group_permissions(session, group_id, permissions)
    group.updated_at = now_iso()
    session.commit()
    return group_to_read(session, group)
