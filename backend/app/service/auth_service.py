from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import DEFAULT_GROUP_PERMISSIONS, PermissionCode
from app.core.security import create_access_token, verify_password
from app.model import repo_auth


@dataclass(frozen=True)
class Principal:
    username: str
    source: str
    groups: list[str]
    permissions: list[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def _config_principal(username: str, group: str) -> Principal:
    return Principal(
        username=username,
        source="config",
        groups=[group],
        permissions=list(DEFAULT_GROUP_PERMISSIONS[group]),
    )


def authenticate_config_user(username: str, password: str) -> Principal | None:
    settings = get_settings()
    if settings.admin_username and settings.admin_password:
        if username == settings.admin_username and password == settings.admin_password:
            return _config_principal(username, "admin")
    if settings.bot_username and settings.bot_password:
        if username == settings.bot_username and password == settings.bot_password:
            return _config_principal(username, "bot")
    return None


def authenticate_db_user(session: Session, username: str, password: str) -> Principal | None:
    user = repo_auth.get_user_by_username(session, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    groups = repo_auth.group_names_for_user(session, user.user_id)
    permissions = sorted(repo_auth.permissions_for_user(session, user.user_id))
    repo_auth.touch_user_login(session, user.user_id)
    session.commit()
    return Principal(username=user.username, source="db", groups=groups, permissions=permissions)


def authenticate(session: Session, username: str, password: str) -> Principal | None:
    config_principal = authenticate_config_user(username, password)
    if config_principal is not None:
        return config_principal
    return authenticate_db_user(session, username, password)


def issue_token(principal: Principal) -> tuple[str, datetime]:
    settings = get_settings()
    if not settings.auth_secret:
        raise RuntimeError("AEO_AUTH_SECRET not configured")
    ttl_seconds = settings.access_token_ttl_minutes * 60
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    token = create_access_token(
        subject=principal.username,
        source=principal.source,  # type: ignore[arg-type]
        groups=principal.groups,
        permissions=principal.permissions,
        secret=settings.auth_secret,
        ttl_seconds=ttl_seconds,
    )
    return token, expires_at


def dev_principal() -> Principal:
    return Principal(
        username="dev",
        source="dev",
        groups=["admin"],
        permissions=list(PermissionCode.all()),
    )
