from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.core.security import hash_password
from app.model import repo_auth
from app.model.tables import User
from app.service.errors import ConflictError, NotFoundError


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
