# User Auth RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global shared token with username/password login, signed Bearer tokens, DB users, config emergency users, RBAC groups, group management, and permission-gated access across the FastAPI API and React SPA.

**Architecture:** Backend auth is split into focused model/repo/schema/service/API files. Config users authenticate without DB access; DB users use password hashes and RBAC tables. Frontend auth is centralized in `frontend/app/lib/api.ts` and `frontend/app/lib/auth.ts`, with route guards and permission-aware navigation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, passlib bcrypt, PyJWT, pytest, React 19, TypeScript, Vite, TanStack Query, sonner.

---

## File Structure

Backend files to create:

- `backend/app/core/security.py`: password hashing, token signing, token verification.
- `backend/app/core/permissions.py`: built-in permission codes, built-in groups, default group-permission mapping.
- `backend/app/model/repo_auth.py`: user, group, permission, membership, and group-permission DB operations.
- `backend/app/schema/auth.py`: login, token, principal, and current-user schemas.
- `backend/app/schema/users.py`: user management request/response schemas.
- `backend/app/schema/groups.py`: group management and permission schemas.
- `backend/app/service/auth_service.py`: config-user auth, DB-user auth, token issuing, current principal loading.
- `backend/app/service/rbac_service.py`: RBAC bootstrap, permission resolution, group/user management business rules.
- `backend/app/api/routes/auth.py`: `/api/auth/login`, `/api/auth/me`.
- `backend/app/api/routes/users.py`: `/api/users` CRUD-style endpoints.
- `backend/app/api/routes/groups.py`: `/api/groups`, `/api/permissions`.
- `backend/alembic/versions/0002_auth_rbac.py`: RBAC tables and seed data.
- `backend/tests/core/test_security.py`: password and token tests.
- `backend/tests/model/test_repo_auth.py`: repository and RBAC bootstrap tests.
- `backend/tests/service/test_auth_service.py`: DB/config login tests.
- `backend/tests/api/test_auth_api.py`: auth API tests.
- `backend/tests/api/test_users_api.py`: user management API tests.
- `backend/tests/api/test_groups_api.py`: group management API tests.
- `backend/tests/api/test_rbac_existing_routes.py`: permission and ownership checks on existing routes.

Backend files to modify:

- `backend/pyproject.toml`: add `passlib[bcrypt]` and `PyJWT`.
- `backend/app/core/config.py`: add auth settings and remove `AEO_TOKEN` as the primary auth mechanism.
- `backend/app/model/tables.py`: add `User`, `Group`, `Permission`, `UserGroup`, `GroupPermission`.
- `backend/app/api/deps.py`: replace `require_token` with principal and permission dependencies.
- `backend/app/api/router.py`: register auth/users/groups routes and attach route-specific dependencies.
- `backend/app/main.py`: call RBAC bootstrap at startup and keep `/api/health` public.
- `backend/app/schema/runs.py`: remove client-controlled owner from create request or make it ignored.
- `backend/app/api/routes/runs.py`: use current principal, enforce create/read/manage permissions and owner scope.
- `backend/app/api/routes/templates.py`: enforce current-principal owner scope.
- `backend/app/api/routes/workers.py`: split read/manage permissions.
- `backend/app/api/routes/worker_protocol.py`: require bot/admin worker permissions.
- `backend/app/api/routes/enroll.py`: require admin enroll permission and render bot login credentials.
- `backend/app/service/enroll_service.py`: render scripts that log in with bot username/password.
- `backend/app/worker/daemon.py`: log in with bot credentials and send Bearer tokens.
- `backend/tests/conftest.py`: provide authenticated test clients and RBAC bootstrapping.
- `.env.example`: replace shared token docs with admin/bot/auth-secret settings.
- `README.md`: update auth and worker enrollment instructions.

Frontend files to create:

- `frontend/app/lib/auth.ts`: token storage, current-user query helpers, permission helpers.
- `frontend/app/routes/login.tsx`: login page.
- `frontend/app/routes/users.tsx`: user management page.
- `frontend/app/routes/groups.tsx`: group management page.

Frontend files to modify:

- `frontend/app/lib/api.ts`: send `Authorization: Bearer`, clear token on 401, add PATCH helper.
- `frontend/app/lib/types.ts`: add auth, user, group, permission types.
- `frontend/app/main.tsx`: add `/login`, `/users`, `/groups` routes.
- `frontend/app/root.tsx`: guard authenticated routes, show current user, show admin nav entries.
- `frontend/app/routes/create.tsx`: remove owner input and do not submit owner.
- `frontend/app/routes/tasks.tsx`: add admin owner filter support if backend exposes all runs.
- `frontend/app/routes/workers.tsx`: hide management actions without `workers.manage`; generate enroll flow without URL token.

## Task 1: Backend Security Primitives and Auth Settings

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/core/security.py`
- Test: `backend/tests/core/test_security.py`

- [ ] **Step 1: Add the failing security tests**

Create `backend/tests/core/test_security.py`:

```python
import time

import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)


def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_access_token_round_trip():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret="unit-secret",
        ttl_seconds=60,
    )

    payload = verify_access_token(token, secret="unit-secret")

    assert payload.subject == "alice"
    assert payload.source == "db"
    assert payload.groups == ["user"]
    assert payload.permissions == ["tasks.create"]


def test_access_token_rejects_wrong_secret():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret="unit-secret",
        ttl_seconds=60,
    )

    with pytest.raises(InvalidTokenError):
        verify_access_token(token, secret="other-secret")


def test_access_token_rejects_expired_token():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret="unit-secret",
        ttl_seconds=-1,
    )
    time.sleep(0.01)

    with pytest.raises(InvalidTokenError):
        verify_access_token(token, secret="unit-secret")
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
cd backend && uv run pytest tests/core/test_security.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.security'`.

- [ ] **Step 3: Add auth dependencies**

Modify `backend/pyproject.toml` dependencies:

```toml
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "python-multipart>=0.0.9",
  "passlib[bcrypt]>=1.7.4",
  "PyJWT>=2.8",
]
```

- [ ] **Step 4: Add auth settings**

Modify `backend/app/core/config.py` `Settings` fields:

```python
    token: str | None = Field(default=None, alias="AEO_TOKEN")
    allow_no_auth: bool = Field(default=False, alias="AEO_ALLOW_NO_AUTH")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    frontend_dist: str = Field(default="frontend/dist", alias="AEO_FRONTEND_DIST")
    admin_username: str | None = Field(default=None, alias="AEO_ADMIN_USERNAME")
    admin_password: str | None = Field(default=None, alias="AEO_ADMIN_PASSWORD")
    bot_username: str | None = Field(default=None, alias="AEO_BOT_USERNAME")
    bot_password: str | None = Field(default=None, alias="AEO_BOT_PASSWORD")
    auth_secret: str | None = Field(default=None, alias="AEO_AUTH_SECRET")
    access_token_ttl_minutes: int = Field(default=480, alias="AEO_ACCESS_TOKEN_TTL_MINUTES")
```

Keep `token` for migration warnings and tests that still inspect settings. Do not use it for authorization after Task 4.

- [ ] **Step 5: Implement security primitives**

Create `backend/app/core/security.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from jwt import InvalidTokenError as JwtInvalidTokenError
from passlib.context import CryptContext


_password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class InvalidTokenError(ValueError):
    pass


@dataclass(frozen=True)
class TokenPayload:
    subject: str
    source: Literal["config", "db", "dev"]
    groups: list[str]
    permissions: list[str]
    expires_at: datetime


def hash_password(password: str) -> str:
    return _password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_context.verify(password, password_hash)


def create_access_token(
    *,
    subject: str,
    source: Literal["config", "db", "dev"],
    groups: list[str],
    permissions: list[str],
    secret: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": subject,
        "source": source,
        "groups": groups,
        "permissions": permissions,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_access_token(token: str, *, secret: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JwtInvalidTokenError as exc:
        raise InvalidTokenError("invalid access token") from exc

    subject = payload.get("sub")
    source = payload.get("source")
    groups = payload.get("groups")
    permissions = payload.get("permissions")
    exp = payload.get("exp")
    if (
        not isinstance(subject, str)
        or source not in {"config", "db", "dev"}
        or not isinstance(groups, list)
        or not all(isinstance(item, str) for item in groups)
        or not isinstance(permissions, list)
        or not all(isinstance(item, str) for item in permissions)
        or not isinstance(exp, int)
    ):
        raise InvalidTokenError("malformed access token")
    return TokenPayload(
        subject=subject,
        source=source,
        groups=list(groups),
        permissions=list(permissions),
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
    )
```

- [ ] **Step 6: Run security tests**

Run:

```bash
cd backend && uv run pytest tests/core/test_security.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/core/config.py backend/app/core/security.py backend/tests/core/test_security.py
git commit -m "feat: add auth security primitives"
```

## Task 2: RBAC Tables, Defaults, Migration, and Repositories

**Files:**
- Create: `backend/app/core/permissions.py`
- Modify: `backend/app/model/tables.py`
- Create: `backend/app/model/repo_auth.py`
- Create: `backend/alembic/versions/0002_auth_rbac.py`
- Test: `backend/tests/model/test_repo_auth.py`

- [ ] **Step 1: Add failing repository tests**

Create `backend/tests/model/test_repo_auth.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/model/test_repo_auth.py -v
```

Expected: FAIL with `ImportError` for `repo_auth` or `PermissionCode`.

- [ ] **Step 3: Add built-in permission definitions**

Create `backend/app/core/permissions.py`:

```python
from __future__ import annotations


class PermissionCode:
    USERS_MANAGE = "users.manage"
    GROUPS_MANAGE = "groups.manage"
    WORKERS_READ = "workers.read"
    WORKERS_MANAGE = "workers.manage"
    TASKS_CREATE = "tasks.create"
    TASKS_READ_OWN = "tasks.read_own"
    TASKS_MANAGE_OWN = "tasks.manage_own"
    TASKS_READ_ALL = "tasks.read_all"
    TASKS_MANAGE_ALL = "tasks.manage_all"
    WORKER_PROTOCOL_USE = "worker_protocol.use"
    ASSETS_USE = "assets.use"
    ENROLL_MANAGE = "enroll.manage"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.USERS_MANAGE,
            cls.GROUPS_MANAGE,
            cls.WORKERS_READ,
            cls.WORKERS_MANAGE,
            cls.TASKS_CREATE,
            cls.TASKS_READ_OWN,
            cls.TASKS_MANAGE_OWN,
            cls.TASKS_READ_ALL,
            cls.TASKS_MANAGE_ALL,
            cls.WORKER_PROTOCOL_USE,
            cls.ASSETS_USE,
            cls.ENROLL_MANAGE,
        ]


PERMISSION_DESCRIPTIONS: dict[str, str] = {
    PermissionCode.USERS_MANAGE: "管理数据库用户",
    PermissionCode.GROUPS_MANAGE: "管理分组和分组权限",
    PermissionCode.WORKERS_READ: "查看基础机器状态",
    PermissionCode.WORKERS_MANAGE: "添加、启停、删除机器",
    PermissionCode.TASKS_CREATE: "创建评测任务",
    PermissionCode.TASKS_READ_OWN: "查看自己的任务",
    PermissionCode.TASKS_MANAGE_OWN: "操作自己的任务",
    PermissionCode.TASKS_READ_ALL: "查看全部任务",
    PermissionCode.TASKS_MANAGE_ALL: "操作全部任务",
    PermissionCode.WORKER_PROTOCOL_USE: "使用 worker 协议",
    PermissionCode.ASSETS_USE: "拉取资产和上传结果归档",
    PermissionCode.ENROLL_MANAGE: "生成和访问 enroll 脚本",
}

BUILTIN_GROUPS: dict[str, dict[str, str]] = {
    "admin": {"display_name": "管理员组", "description": "拥有系统全部权限"},
    "user": {"display_name": "普通用户组", "description": "创建和管理自己的评测任务"},
    "bot": {"display_name": "机器用户组", "description": "worker 机器通信使用"},
}

DEFAULT_GROUP_PERMISSIONS: dict[str, list[str]] = {
    "admin": PermissionCode.all(),
    "user": [
        PermissionCode.TASKS_CREATE,
        PermissionCode.TASKS_READ_OWN,
        PermissionCode.TASKS_MANAGE_OWN,
        PermissionCode.WORKERS_READ,
    ],
    "bot": [
        PermissionCode.WORKER_PROTOCOL_USE,
        PermissionCode.ASSETS_USE,
    ],
}
```

- [ ] **Step 4: Add ORM tables**

Modify `backend/app/model/tables.py` imports:

```python
from sqlalchemy import JSON, Float, Integer, String, Text, UniqueConstraint
```

Add these classes before `TaskTemplate`:

```python
class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    last_login_at: Mapped[str | None] = mapped_column(String, nullable=True)


class Group(Base):
    __tablename__ = "groups"
    group_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_builtin: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Permission(Base):
    __tablename__ = "permissions"
    permission_id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UserGroup(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_groups_user_group"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String, nullable=False, index=True)


class GroupPermission(Base):
    __tablename__ = "group_permissions"
    __table_args__ = (UniqueConstraint("group_id", "permission_id", name="uq_group_permissions_group_permission"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    permission_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
```

- [ ] **Step 5: Add auth repository**

Create `backend/app/model/repo_auth.py` with functions used by tests:

```python
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.core.permissions import BUILTIN_GROUPS, DEFAULT_GROUP_PERMISSIONS, PERMISSION_DESCRIPTIONS
from app.model.tables import Group, GroupPermission, Permission, User, UserGroup


def bootstrap_rbac(session: Session) -> None:
    permissions_by_code = {p.code: p for p in session.scalars(select(Permission)).all()}
    for code, description in PERMISSION_DESCRIPTIONS.items():
        if code not in permissions_by_code:
            session.add(Permission(permission_id=new_id("perm"), code=code, description=description))
    session.flush()

    groups_by_name = {g.name: g for g in session.scalars(select(Group)).all()}
    for name, meta in BUILTIN_GROUPS.items():
        group = groups_by_name.get(name)
        if group is None:
            group = Group(
                group_id=new_id("grp"),
                name=name,
                display_name=meta["display_name"],
                description=meta["description"],
                is_builtin=True,
                is_active=True,
            )
            session.add(group)
        else:
            group.is_builtin = True
            group.is_active = True
    session.flush()

    for group_name, permission_codes in DEFAULT_GROUP_PERMISSIONS.items():
        group = get_group_by_name(session, group_name)
        assert group is not None
        set_group_permissions(session, group.group_id, permission_codes)


def list_permissions(session: Session) -> list[Permission]:
    return list(session.scalars(select(Permission).order_by(Permission.code)).all())


def list_groups(session: Session, *, include_inactive: bool = True) -> list[Group]:
    stmt = select(Group).order_by(Group.name)
    if not include_inactive:
        stmt = stmt.where(Group.is_active == 1)
    return list(session.scalars(stmt).all())


def get_group(session: Session, group_id: str) -> Group | None:
    return session.get(Group, group_id)


def get_group_by_name(session: Session, name: str) -> Group | None:
    return session.scalar(select(Group).where(Group.name == name))


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def list_users(session: Session, *, include_inactive: bool = True) -> list[User]:
    stmt = select(User).order_by(User.username)
    if not include_inactive:
        stmt = stmt.where(User.is_active == 1)
    return list(session.scalars(stmt).all())


def create_group(session: Session, *, name: str, display_name: str, description: str) -> Group:
    group = Group(
        group_id=new_id("grp"),
        name=name,
        display_name=display_name,
        description=description,
        is_builtin=False,
        is_active=True,
    )
    session.add(group)
    session.flush()
    return group


def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    group_names: list[str],
) -> User:
    user = User(
        user_id=new_id("usr"),
        username=username,
        display_name=display_name,
        password_hash=password_hash,
        is_active=True,
    )
    session.add(user)
    session.flush()
    set_user_groups(session, user.user_id, group_names)
    return user


def set_user_groups(session: Session, user_id: str, group_names: list[str]) -> None:
    groups = list(session.scalars(select(Group).where(Group.name.in_(group_names), Group.is_active == 1)).all())
    found = {group.name for group in groups}
    missing = sorted(set(group_names) - found)
    if missing:
        raise ValueError(f"unknown or inactive groups: {', '.join(missing)}")
    session.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
    for group in groups:
        session.add(UserGroup(user_id=user_id, group_id=group.group_id))
    session.flush()


def set_group_permissions(session: Session, group_id: str, permission_codes: list[str]) -> None:
    permissions = list(session.scalars(select(Permission).where(Permission.code.in_(permission_codes))).all())
    found = {permission.code for permission in permissions}
    missing = sorted(set(permission_codes) - found)
    if missing:
        raise ValueError(f"unknown permissions: {', '.join(missing)}")
    session.execute(delete(GroupPermission).where(GroupPermission.group_id == group_id))
    for permission in permissions:
        session.add(GroupPermission(group_id=group_id, permission_id=permission.permission_id))
    session.flush()


def group_names_for_user(session: Session, user_id: str) -> list[str]:
    stmt = (
        select(Group.name)
        .join(UserGroup, UserGroup.group_id == Group.group_id)
        .where(UserGroup.user_id == user_id, Group.is_active == 1)
        .order_by(Group.name)
    )
    return list(session.scalars(stmt).all())


def permissions_for_group(session: Session, group_name: str) -> set[str]:
    stmt = (
        select(Permission.code)
        .join(GroupPermission, GroupPermission.permission_id == Permission.permission_id)
        .join(Group, Group.group_id == GroupPermission.group_id)
        .where(Group.name == group_name, Group.is_active == 1)
    )
    return set(session.scalars(stmt).all())


def permissions_for_user(session: Session, user_id: str) -> set[str]:
    stmt = (
        select(Permission.code)
        .join(GroupPermission, GroupPermission.permission_id == Permission.permission_id)
        .join(Group, Group.group_id == GroupPermission.group_id)
        .join(UserGroup, UserGroup.group_id == Group.group_id)
        .where(UserGroup.user_id == user_id, Group.is_active == 1)
    )
    return set(session.scalars(stmt).all())


def touch_user_login(session: Session, user_id: str) -> None:
    user = get_user(session, user_id)
    if user is not None:
        user.last_login_at = now_iso()
```

- [ ] **Step 6: Add Alembic migration**

Create `backend/alembic/versions/0002_auth_rbac.py`:

```python
"""add auth rbac tables

Revision ID: 0002_auth_rbac
Revises: 0001_init
Create Date: 2026-06-07 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.permissions import BUILTIN_GROUPS, DEFAULT_GROUP_PERMISSIONS, PERMISSION_DESCRIPTIONS

revision: str = "0002_auth_rbac"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_login_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "groups",
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_builtin", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("group_id"),
    )
    op.create_index(op.f("ix_groups_name"), "groups", ["name"], unique=True)
    op.create_table(
        "permissions",
        sa.Column("permission_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("permission_id"),
    )
    op.create_index(op.f("ix_permissions_code"), "permissions", ["code"], unique=True)
    op.create_table(
        "user_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_groups_user_group"),
    )
    op.create_index(op.f("ix_user_groups_user_id"), "user_groups", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_groups_group_id"), "user_groups", ["group_id"], unique=False)
    op.create_table(
        "group_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("permission_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "permission_id", name="uq_group_permissions_group_permission"),
    )
    op.create_index(op.f("ix_group_permissions_group_id"), "group_permissions", ["group_id"], unique=False)
    op.create_index(op.f("ix_group_permissions_permission_id"), "group_permissions", ["permission_id"], unique=False)

    permissions = []
    for index, (code, description) in enumerate(PERMISSION_DESCRIPTIONS.items(), start=1):
        permission_id = f"perm-seed-{index:03d}"
        permissions.append({"permission_id": permission_id, "code": code, "description": description})
    op.bulk_insert(sa.table("permissions", sa.column("permission_id"), sa.column("code"), sa.column("description")), permissions)

    groups = []
    for index, (name, meta) in enumerate(BUILTIN_GROUPS.items(), start=1):
        groups.append({
            "group_id": f"grp-seed-{index:03d}",
            "name": name,
            "display_name": meta["display_name"],
            "description": meta["description"],
            "is_builtin": 1,
            "is_active": 1,
            "created_at": "2026-06-07T00:00:00Z",
            "updated_at": "2026-06-07T00:00:00Z",
        })
    op.bulk_insert(
        sa.table(
            "groups",
            sa.column("group_id"),
            sa.column("name"),
            sa.column("display_name"),
            sa.column("description"),
            sa.column("is_builtin"),
            sa.column("is_active"),
            sa.column("created_at"),
            sa.column("updated_at"),
        ),
        groups,
    )

    permission_ids = {item["code"]: item["permission_id"] for item in permissions}
    group_ids = {item["name"]: item["group_id"] for item in groups}
    group_permissions = []
    counter = 1
    for group_name, codes in DEFAULT_GROUP_PERMISSIONS.items():
        for code in codes:
            group_permissions.append({
                "id": counter,
                "group_id": group_ids[group_name],
                "permission_id": permission_ids[code],
            })
            counter += 1
    op.bulk_insert(
        sa.table("group_permissions", sa.column("id"), sa.column("group_id"), sa.column("permission_id")),
        group_permissions,
    )


def downgrade() -> None:
    op.drop_table("group_permissions")
    op.drop_table("user_groups")
    op.drop_index(op.f("ix_permissions_code"), table_name="permissions")
    op.drop_table("permissions")
    op.drop_index(op.f("ix_groups_name"), table_name="groups")
    op.drop_table("groups")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
```

- [ ] **Step 7: Run repository and migration tests**

Run:

```bash
cd backend && uv run pytest tests/model/test_repo_auth.py tests/model/test_migrations.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/permissions.py backend/app/model/tables.py backend/app/model/repo_auth.py backend/alembic/versions/0002_auth_rbac.py backend/tests/model/test_repo_auth.py
git commit -m "feat: add rbac persistence"
```

## Task 3: Auth Service, Schemas, and Auth API

**Files:**
- Create: `backend/app/schema/auth.py`
- Create: `backend/app/service/auth_service.py`
- Modify: `backend/app/api/deps.py`
- Create: `backend/app/api/routes/auth.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/service/test_auth_service.py`
- Test: `backend/tests/api/test_auth_api.py`

- [ ] **Step 1: Add service tests**

Create `backend/tests/service/test_auth_service.py`:

```python
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
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")

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
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")

    principal = auth_service.authenticate_config_user("worker-bot", "secret")

    assert principal is not None
    assert principal.username == "worker-bot"
    assert principal.groups == ["bot"]
    assert principal.permissions == [PermissionCode.WORKER_PROTOCOL_USE, PermissionCode.ASSETS_USE]


def test_db_user_login(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
```

- [ ] **Step 2: Add API tests**

Create `backend/tests/api/test_auth_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth


def test_login_config_admin(monkeypatch, session):
    monkeypatch.setenv("AEO_ADMIN_USERNAME", "root")
    monkeypatch.setenv("AEO_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})

    assert resp.status_code == 401
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/service/test_auth_service.py tests/api/test_auth_api.py -v
```

Expected: FAIL with missing `auth_service` and missing `/api/auth/login`.

- [ ] **Step 4: Add auth schemas**

Create `backend/app/schema/auth.py`:

```python
from app.schema.common import ApiModel


class LoginRequest(ApiModel):
    username: str
    password: str


class PrincipalRead(ApiModel):
    username: str
    source: str
    groups: list[str]
    permissions: list[str]


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: PrincipalRead
```

- [ ] **Step 5: Add auth service**

Create `backend/app/service/auth_service.py`:

```python
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
```

- [ ] **Step 6: Add initial current-principal dependency**

Modify `backend/app/api/deps.py` so it keeps `db_session()` and adds `require_current_principal()` while leaving `require_token()` in place until Task 4 replaces the protected router:

```python
from __future__ import annotations

from fastapi import HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import InvalidTokenError, verify_access_token
from app.model.db import get_db
from app.service.auth_service import Principal, dev_principal


def db_session() -> Session:
    yield from get_db()


def require_current_principal(request: Request) -> Principal:
    settings = get_settings()
    if settings.allow_no_auth:
        return dev_principal()
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if not settings.auth_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AEO_AUTH_SECRET not configured")
    try:
        payload = verify_access_token(token, secret=settings.auth_secret)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from None
    return Principal(
        username=payload.subject,
        source=payload.source,
        groups=payload.groups,
        permissions=payload.permissions,
    )


def require_token(request: Request, token: str | None = Query(default=None)) -> None:
    settings = get_settings()
    expected = settings.token
    if not expected:
        if settings.allow_no_auth:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AEO_TOKEN not configured (set AEO_TOKEN, or AEO_ALLOW_NO_AUTH=1 for local dev)",
        )
    header = request.headers.get("X-AEO-Token")
    cookie = request.cookies.get("aeo_token")
    if token == expected or header == expected or cookie == expected:
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
```

- [ ] **Step 7: Add auth routes**

Create `backend/app/api/routes/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_current_principal
from app.schema.auth import LoginRequest, PrincipalRead, TokenResponse
from app.service import auth_service

router = APIRouter()


def _principal_read(principal: auth_service.Principal) -> PrincipalRead:
    return PrincipalRead(
        username=principal.username,
        source=principal.source,
        groups=principal.groups,
        permissions=principal.permissions,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(db_session)) -> TokenResponse:
    principal = auth_service.authenticate(session, body.username, body.password)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")
    token, expires_at = auth_service.issue_token(principal)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires_at.isoformat(),
        user=_principal_read(principal),
    )


@router.get("/auth/me", response_model=PrincipalRead)
def me(principal: auth_service.Principal = Depends(require_current_principal)) -> PrincipalRead:
    return _principal_read(principal)
```

- [ ] **Step 8: Wire auth route and RBAC bootstrap**

Modify `backend/app/api/router.py` imports and include auth route in public API:

```python
from app.api.routes import (
    auth,
    batches,
    case_runs,
    dashboard,
    datasets,
    enroll,
    files,
    harbor_viewer,
    health,
    runs,
    templates,
    worker_protocol,
    workers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
```

Modify `backend/app/main.py` startup in `lifespan` before orchestration starts:

```python
    with get_session() as session:
        from app.model import repo_auth

        repo_auth.bootstrap_rbac(session)
```

- [ ] **Step 9: Run auth tests**

Run:

```bash
cd backend && uv run pytest tests/service/test_auth_service.py tests/api/test_auth_api.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/schema/auth.py backend/app/service/auth_service.py backend/app/api/deps.py backend/app/api/routes/auth.py backend/app/api/router.py backend/app/main.py backend/tests/service/test_auth_service.py backend/tests/api/test_auth_api.py
git commit -m "feat: add login and current user api"
```

## Task 4: Principal Dependencies and Permission Gates

**Files:**
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/api/test_auth_api.py`
- Test: `backend/tests/conftest.py`

- [ ] **Step 1: Add dependency tests**

Append to `backend/tests/api/test_auth_api.py`:

```python
def test_protected_route_requires_bearer_token(monkeypatch, session):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.get("/api/task-templates")

    assert resp.status_code == 401


def test_old_shared_token_header_no_longer_authorizes(monkeypatch, session):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)

    resp = client.get("/api/task-templates", headers={"X-AEO-Token": "secret"})

    assert resp.status_code == 401
```

- [ ] **Step 2: Run dependency tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/api/test_auth_api.py::test_protected_route_requires_bearer_token tests/api/test_auth_api.py::test_old_shared_token_header_no_longer_authorizes -v
```

Expected: FAIL because current `require_token` still accepts shared token or dev-open auth.

- [ ] **Step 3: Replace `require_token` with permission helpers**

Replace `backend/app/api/deps.py` with:

```python
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import InvalidTokenError, verify_access_token
from app.model.db import get_db
from app.service.auth_service import Principal, dev_principal


def db_session() -> Session:
    yield from get_db()


def require_current_principal(request: Request) -> Principal:
    settings = get_settings()
    if settings.allow_no_auth:
        return dev_principal()
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if not settings.auth_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AEO_AUTH_SECRET not configured")
    try:
        payload = verify_access_token(token, secret=settings.auth_secret)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from None
    return Principal(
        username=payload.subject,
        source=payload.source,
        groups=payload.groups,
        permissions=payload.permissions,
    )


def require_permission(permission: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Principal = Depends(require_current_principal)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission: {permission}")
        return principal

    return dependency
```

- [ ] **Step 4: Replace global authed router dependency**

Modify `backend/app/api/router.py`:

```python
from fastapi import APIRouter, Depends

from app.api.deps import require_current_principal

from app.api.routes import (
    auth,
    batches,
    case_runs,
    dashboard,
    datasets,
    enroll,
    files,
    harbor_viewer,
    health,
    runs,
    templates,
    worker_protocol,
    workers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])

authed_router = APIRouter(dependencies=[Depends(require_current_principal)])
authed_router.include_router(templates.router, tags=["templates"])
authed_router.include_router(workers.router, tags=["workers"])
authed_router.include_router(datasets.router, tags=["datasets"])
authed_router.include_router(dashboard.router, tags=["dashboard"])
authed_router.include_router(runs.router, tags=["runs"])
authed_router.include_router(case_runs.router, tags=["case-runs"])
authed_router.include_router(batches.router, tags=["batches"])
authed_router.include_router(worker_protocol.router, tags=["worker-protocol"])
authed_router.include_router(files.router, tags=["files"])
authed_router.include_router(harbor_viewer.router, tags=["harbor-viewer"])
authed_router.include_router(enroll.router, tags=["enroll"])
```

This preserves the invariant that every non-health, non-login API route requires a Bearer token. Route-level permission dependencies are added in later tasks.

- [ ] **Step 5: Run auth API tests**

Run:

```bash
cd backend && uv run pytest tests/api/test_auth_api.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/deps.py backend/app/api/router.py backend/tests/api/test_auth_api.py
git commit -m "feat: require bearer principals"
```

## Task 5: User Management API

**Files:**
- Create: `backend/app/schema/users.py`
- Create: `backend/app/service/rbac_service.py`
- Create: `backend/app/api/routes/users.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/api/test_users_api.py`

- [ ] **Step 1: Add failing user API tests**

Create `backend/tests/api/test_users_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth


def _client(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/api/test_users_api.py -v
```

Expected: FAIL with 404 for `/api/users`.

- [ ] **Step 3: Add user schemas**

Create `backend/app/schema/users.py`:

```python
from app.schema.common import ApiModel


class UserCreate(ApiModel):
    username: str
    display_name: str
    password: str
    groups: list[str]


class UserUpdate(ApiModel):
    display_name: str | None = None
    is_active: bool | None = None
    groups: list[str] | None = None


class PasswordReset(ApiModel):
    password: str


class UserRead(ApiModel):
    user_id: str
    username: str
    display_name: str
    is_active: bool
    groups: list[str]
    created_at: str
    updated_at: str
    last_login_at: str | None = None
```

- [ ] **Step 4: Add RBAC service user functions**

Create `backend/app/service/rbac_service.py`:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.core.security import hash_password
from app.model import repo_auth
from app.model.tables import User
from app.service.errors import NotFoundError, ServiceError


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
        raise ServiceError(f"user already exists: {username}", status_code=409)
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
```

- [ ] **Step 5: Add user routes**

Create `backend/app/api/routes/users.py`:

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.users import PasswordReset, UserCreate, UserRead, UserUpdate
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.USERS_MANAGE))])


@router.get("/users")
def list_users(session: Session = Depends(db_session)) -> dict:
    return {"users": [UserRead.model_validate(item).model_dump(by_alias=True) for item in rbac_service.list_users(session)]}


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, session: Session = Depends(db_session)) -> UserRead:
    item = rbac_service.create_user(
        session,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        groups=body.groups,
    )
    return UserRead.model_validate(item)


@router.get("/users/{user_id}", response_model=UserRead)
def get_user(user_id: str, session: Session = Depends(db_session)) -> UserRead:
    return UserRead.model_validate(rbac_service.get_user_read(session, user_id))


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(user_id: str, body: UserUpdate, session: Session = Depends(db_session)) -> UserRead:
    return UserRead.model_validate(
        rbac_service.update_user(
            session,
            user_id,
            display_name=body.display_name,
            is_active=body.is_active,
            groups=body.groups,
        )
    )


@router.delete("/users/{user_id}")
def delete_user(user_id: str, session: Session = Depends(db_session)) -> dict:
    rbac_service.disable_user(session, user_id)
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, body: PasswordReset, session: Session = Depends(db_session)) -> dict:
    rbac_service.reset_password(session, user_id, body.password)
    return {"ok": True}
```

Remove the unused `Principal` import if the linter flags it.

- [ ] **Step 6: Register user routes**

Modify `backend/app/api/router.py` imports to include `users`, then include:

```python
authed_router.include_router(users.router, tags=["users"])
```

- [ ] **Step 7: Run user API tests**

Run:

```bash
cd backend && uv run pytest tests/api/test_users_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schema/users.py backend/app/service/rbac_service.py backend/app/api/routes/users.py backend/app/api/router.py backend/tests/api/test_users_api.py
git commit -m "feat: add user management api"
```

## Task 6: Group Management API

**Files:**
- Create: `backend/app/schema/groups.py`
- Modify: `backend/app/service/rbac_service.py`
- Create: `backend/app/api/routes/groups.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/api/test_groups_api.py`

- [ ] **Step 1: Add failing group API tests**

Create `backend/tests/api/test_groups_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.permissions import PermissionCode
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth


def _client(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/api/test_groups_api.py -v
```

Expected: FAIL with 404 for `/api/groups`.

- [ ] **Step 3: Add group schemas**

Create `backend/app/schema/groups.py`:

```python
from app.schema.common import ApiModel


class GroupCreate(ApiModel):
    name: str
    display_name: str
    description: str = ""


class GroupUpdate(ApiModel):
    display_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class PermissionAssignment(ApiModel):
    permissions: list[str]


class PermissionRead(ApiModel):
    code: str
    description: str


class GroupRead(ApiModel):
    group_id: str
    name: str
    display_name: str
    description: str
    is_builtin: bool
    is_active: bool
    permissions: list[str]
```

- [ ] **Step 4: Add group service functions**

Append to `backend/app/service/rbac_service.py`:

```python
from app.core.permissions import PermissionCode
from app.model.tables import Group


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
        raise ServiceError(f"group already exists: {name}", status_code=409)
    group = repo_auth.create_group(session, name=name, display_name=display_name, description=description)
    session.commit()
    return group_to_read(session, group)


def _require_group(session: Session, group_id: str) -> Group:
    group = repo_auth.get_group(session, group_id)
    if group is None:
        raise NotFoundError(f"group not found: {group_id}")
    return group


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
        raise ServiceError(f"builtin group cannot be disabled: {group.name}", status_code=400)
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
        raise ServiceError(f"builtin group cannot be deleted: {group.name}", status_code=400)
    group.is_active = False
    group.updated_at = now_iso()
    session.commit()


def set_group_permissions(session: Session, group_id: str, permissions: list[str]) -> dict:
    group = _require_group(session, group_id)
    permission_set = set(permissions)
    if group.name == "admin" and not {PermissionCode.USERS_MANAGE, PermissionCode.GROUPS_MANAGE}.issubset(permission_set):
        raise ServiceError("admin group must keep users.manage and groups.manage", status_code=400)
    if group.name == "bot" and not {PermissionCode.WORKER_PROTOCOL_USE, PermissionCode.ASSETS_USE}.issubset(permission_set):
        raise ServiceError("bot group must keep worker_protocol.use and assets.use", status_code=400)
    repo_auth.set_group_permissions(session, group_id, permissions)
    group.updated_at = now_iso()
    session.commit()
    return group_to_read(session, group)
```

- [ ] **Step 5: Add group routes**

Create `backend/app/api/routes/groups.py`:

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.groups import GroupCreate, GroupRead, GroupUpdate, PermissionAssignment, PermissionRead
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.GROUPS_MANAGE))])


@router.get("/groups")
def list_groups(session: Session = Depends(db_session)) -> dict:
    return {"groups": [GroupRead.model_validate(item).model_dump(by_alias=True) for item in rbac_service.list_groups(session)]}


@router.post("/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(
        rbac_service.create_group(session, name=body.name, display_name=body.display_name, description=body.description)
    )


@router.get("/groups/{group_id}", response_model=GroupRead)
def get_group(group_id: str, session: Session = Depends(db_session)) -> GroupRead:
    groups = [item for item in rbac_service.list_groups(session) if item["group_id"] == group_id]
    if not groups:
        from app.service.errors import NotFoundError

        raise NotFoundError(f"group not found: {group_id}")
    return GroupRead.model_validate(groups[0])


@router.patch("/groups/{group_id}", response_model=GroupRead)
def update_group(group_id: str, body: GroupUpdate, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(
        rbac_service.update_group(
            session,
            group_id,
            display_name=body.display_name,
            description=body.description,
            is_active=body.is_active,
        )
    )


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, session: Session = Depends(db_session)) -> dict:
    rbac_service.disable_group(session, group_id)
    return {"ok": True}


@router.get("/permissions")
def list_permissions(session: Session = Depends(db_session)) -> dict:
    return {"permissions": [PermissionRead.model_validate(item).model_dump(by_alias=True) for item in rbac_service.list_permissions(session)]}


@router.put("/groups/{group_id}/permissions", response_model=GroupRead)
def set_permissions(group_id: str, body: PermissionAssignment, session: Session = Depends(db_session)) -> GroupRead:
    return GroupRead.model_validate(rbac_service.set_group_permissions(session, group_id, body.permissions))
```

- [ ] **Step 6: Register group routes**

Modify `backend/app/api/router.py` imports to include `groups`, then include:

```python
authed_router.include_router(groups.router, tags=["groups"])
```

- [ ] **Step 7: Run group API tests**

Run:

```bash
cd backend && uv run pytest tests/api/test_groups_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schema/groups.py backend/app/service/rbac_service.py backend/app/api/routes/groups.py backend/app/api/router.py backend/tests/api/test_groups_api.py
git commit -m "feat: add group management api"
```

## Task 7: Permission-Gate Existing APIs and Enforce Owner Scope

**Files:**
- Modify: `backend/app/api/routes/templates.py`
- Modify: `backend/app/api/routes/runs.py`
- Modify: `backend/app/api/routes/case_runs.py`
- Modify: `backend/app/api/routes/batches.py`
- Modify: `backend/app/api/routes/dashboard.py`
- Modify: `backend/app/api/routes/workers.py`
- Modify: `backend/app/api/routes/files.py`
- Modify: `backend/app/api/routes/harbor_viewer.py`
- Modify: `backend/app/api/routes/worker_protocol.py`
- Modify: `backend/app/schema/runs.py`
- Modify: `backend/app/service/run_service.py`
- Test: `backend/tests/api/test_rbac_existing_routes.py`

- [ ] **Step 1: Add route RBAC tests**

Create `backend/tests/api/test_rbac_existing_routes.py`:

```python
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.security import hash_password
from app.main import create_app
from app.model import repo_auth, repo_runs


def _client(session, monkeypatch):
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
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
    repo_auth.create_user(session, username="admin", display_name="Admin", password_hash=hash_password("secret"), group_names=["admin"])
    repo_auth.create_user(session, username="alice", display_name="Alice", password_hash=hash_password("secret"), group_names=["user"])
    repo_auth.create_user(session, username="bob", display_name="Bob", password_hash=hash_password("secret"), group_names=["user"])
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/api/test_rbac_existing_routes.py -v
```

Expected: FAIL because existing routes do not enforce permissions and owner scope.

- [ ] **Step 3: Make create request owner server-controlled**

Modify `backend/app/schema/runs.py`:

```python
class CreateDistributeRequest(ApiModel):
    name: str
    dataset_path: str
    bitfun_cli_path: str
    bitfun_config_dir: str
    selected_case_ids: list[str] = []
    worker_ids: list[str] = []
    per_worker_concurrency: int = 1
    executor_config: dict = {}
    model_profile_ref: str | None = None
```

Modify `backend/app/service/run_service.py` function signature:

```python
def create_and_distribute(session: Session, req: CreateDistributeRequest, *, owner: str) -> CreateDistributeResponse:
```

Inside that function, replace every `req.owner` with `owner`.

- [ ] **Step 4: Add helper owner checks in runs route**

Modify `backend/app/api/routes/runs.py` imports:

```python
from fastapi import APIRouter, Body, Depends, HTTPException, status
from app.api.deps import db_session, require_current_principal, require_permission
from app.core.permissions import PermissionCode
from app.service.auth_service import Principal
```

Add:

```python
def _can_read_run(principal: Principal, owner: str) -> bool:
    return PermissionCode.TASKS_READ_ALL in principal.permissions or (
        PermissionCode.TASKS_READ_OWN in principal.permissions and owner == principal.username
    )


def _can_manage_run(principal: Principal, owner: str) -> bool:
    return PermissionCode.TASKS_MANAGE_ALL in principal.permissions or (
        PermissionCode.TASKS_MANAGE_OWN in principal.permissions and owner == principal.username
    )


def _require_read_run(principal: Principal, owner: str) -> None:
    if not _can_read_run(principal, owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="run access denied")


def _require_manage_run(principal: Principal, owner: str) -> None:
    if not _can_manage_run(principal, owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="run access denied")
```

Modify route signatures:

```python
@router.post("/eval-tasks/create-and-distribute", response_model=CreateDistributeResponse, status_code=status.HTTP_201_CREATED)
def create_and_distribute(
    body: CreateDistributeRequest,
    session: Session = Depends(db_session),
    principal: Principal = Depends(require_permission(PermissionCode.TASKS_CREATE)),
) -> CreateDistributeResponse:
    return run_service.create_and_distribute(session, body, owner=principal.username)
```

For `get_run_detail`, load run first and call `_require_read_run(principal, run.owner)`.

For `rerun_exceptions`, load run first and call `_require_manage_run(principal, run.owner)`.

For `get_run_sync`, load run first and call `_require_read_run(principal, run.owner)`.

- [ ] **Step 5: Add permission dependencies to workers and worker protocol**

Modify `backend/app/api/routes/workers.py`:

```python
from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode


@router.get("/workers", dependencies=[Depends(require_permission(PermissionCode.WORKERS_READ))])
def list_workers(...):
    ...


@router.post("/workers/{worker_id}/settings", response_model=WorkerRead, dependencies=[Depends(require_permission(PermissionCode.WORKERS_MANAGE))])
def update_settings(...):
    ...


@router.delete("/workers/{worker_id}", dependencies=[Depends(require_permission(PermissionCode.WORKERS_MANAGE))])
def delete_worker(...):
    ...
```

Modify `backend/app/api/routes/worker_protocol.py` router:

```python
from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.WORKER_PROTOCOL_USE))])
```

For asset file/manifest routes in the same file, either keep `worker_protocol.use` or use per-route `assets.use`. Use `assets.use` for `get_asset_manifest`, `get_asset_file`, and `job_archive`:

```python
@router.get("/workers/assets/{asset_manifest_id}", response_model=AssetManifest, dependencies=[Depends(require_permission(PermissionCode.ASSETS_USE))])
```

- [ ] **Step 6: Add permission dependencies to enroll and read routes**

Modify `backend/app/api/routes/enroll.py`:

```python
from fastapi import APIRouter, Depends, Request
from app.api.deps import require_permission
from app.core.permissions import PermissionCode

router = APIRouter(dependencies=[Depends(require_permission(PermissionCode.ENROLL_MANAGE))])
```

For `templates`, require `tasks.create` on POST and `tasks.read_own` or `tasks.read_all` on GET. The minimal first pass:

```python
@router.post("/task-templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission(PermissionCode.TASKS_CREATE))])
```

Add these route dependencies and ownership checks:

```text
backend/app/api/routes/case_runs.py
  GET routes with run_id: require_current_principal, then allow tasks.read_all or tasks.read_own when the run owner matches principal.username.

backend/app/api/routes/batches.py
  GET routes with batch_id: load the batch, load its run, then allow tasks.read_all or tasks.read_own when the run owner matches principal.username.

backend/app/api/routes/dashboard.py
  Dashboard summary: require tasks.read_all for the first implementation pass because the current endpoint is global.

backend/app/api/routes/files.py
  File/archive routes with batch_id or run_id: load the owning run and apply the same read check.

backend/app/api/routes/harbor_viewer.py
  Viewer routes with run_id or batch_id: load the owning run and apply the same read check.
```

Use the `_require_read_run(principal, owner)` helper shape from `backend/app/api/routes/runs.py` in each file, or move it to `backend/app/api/deps.py` as `require_run_read_access(session, principal, run_id)` if three or more files need the same logic.

- [ ] **Step 7: Run route RBAC tests**

Run:

```bash
cd backend && uv run pytest tests/api/test_rbac_existing_routes.py -v
```

Expected: PASS.

- [ ] **Step 8: Run all backend API tests and update fixtures**

Run:

```bash
cd backend && uv run pytest tests/api -v
```

Expected: Some old tests fail because they used dev-open auth or `X-AEO-Token`.

Update `backend/tests/conftest.py` with helpers:

```python
@pytest.fixture
def authed_client(session, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import db_session
    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.main import create_app
    from app.model import repo_auth

    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    get_settings.cache_clear()
    repo_auth.bootstrap_rbac(session)
    if repo_auth.get_user_by_username(session, "admin") is None:
        repo_auth.create_user(
            session,
            username="admin",
            display_name="Admin",
            password_hash=hash_password("secret"),
            group_names=["admin"],
        )
        session.commit()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    token = login.json()["accessToken"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
```

Then migrate old API tests from `client` to `authed_client` where they require protected routes.

- [ ] **Step 9: Re-run backend API tests**

Run:

```bash
cd backend && uv run pytest tests/api -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/routes backend/app/schema/runs.py backend/app/service/run_service.py backend/tests/api backend/tests/conftest.py
git commit -m "feat: enforce api permissions"
```

## Task 8: Worker and Enroll Username/Password Login Flow

**Files:**
- Modify: `backend/app/service/enroll_service.py`
- Modify: `backend/app/api/routes/enroll.py`
- Modify: `backend/app/worker/daemon.py`
- Modify: `scripts/start-worker.sh`
- Modify: `scripts/enroll.sh.tmpl`
- Test: `backend/tests/api/test_enroll_api.py`
- Test: `backend/tests/worker/test_daemon_asset_pull.py`
- Test: `backend/tests/worker/test_daemon_archive_upload.py`

- [ ] **Step 1: Add worker auth flow tests**

Modify `backend/tests/api/test_enroll_api.py` expected script assertions:

```python
def test_enroll_script_uses_bot_credentials(token_client, monkeypatch):
    monkeypatch.setenv("AEO_BOT_USERNAME", "worker-bot")
    monkeypatch.setenv("AEO_BOT_PASSWORD", "bot-secret")
    resp = token_client.get("/api/workers/enroll.sh")
    assert resp.status_code == 200
    text = resp.text
    assert "AEO_BOT_USERNAME=\"worker-bot\"" in text
    assert "AEO_BOT_PASSWORD=\"bot-secret\"" in text
    assert "AEO_TOKEN" not in text
```

Update worker tests to assert `Authorization`:

```python
assert captured["headers"]["authorization"].startswith("Bearer ")
```

- [ ] **Step 2: Run worker/enroll tests to verify failure**

Run:

```bash
cd backend && uv run pytest tests/api/test_enroll_api.py tests/worker -v
```

Expected: FAIL because scripts and daemon still use `X-AEO-Token`.

- [ ] **Step 3: Add worker login helper in daemon**

Modify `backend/app/worker/daemon.py`:

```python
AUTH_HEADER = "Authorization"


def login(controller_url: str, username: str, password: str) -> str:
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{controller_url.rstrip('/')}/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return str(body["accessToken"])


def _auth_headers(token: str | None) -> dict[str, str]:
    return {AUTH_HEADER: f"Bearer {token}"} if token else {}
```

Replace uses of `X-AEO-Token` with `_auth_headers(token)`.

- [ ] **Step 4: Update worker main loop arguments**

Where the daemon reads configuration, accept `AEO_BOT_USERNAME` and `AEO_BOT_PASSWORD`. Before register/claim loop:

```python
token = login(controller_url, bot_username, bot_password)
```

If any request returns 401, log in again once and retry that request.

- [ ] **Step 5: Update enroll service and template**

Modify `backend/app/api/routes/enroll.py` so it reads bot settings:

```python
settings = get_settings()
if not settings.bot_username or not settings.bot_password:
    raise HTTPException(status_code=503, detail="AEO_BOT_USERNAME and AEO_BOT_PASSWORD not configured")
script = enroll_service.render_enroll_script(
    controller_url=_controller_url(request),
    bot_username=settings.bot_username,
    bot_password=settings.bot_password,
    worker_id=worker_id or new_id("worker"),
)
```

Modify `backend/app/service/enroll_service.py` function signature:

```python
def render_enroll_script(*, controller_url: str, bot_username: str, bot_password: str, worker_id: str) -> str:
```

Update `scripts/enroll.sh.tmpl` placeholders:

```bash
export AEO_CONTROLLER_URL="{{CONTROLLER_URL}}"
export AEO_WORKER_ID="{{WORKER_ID}}"
export AEO_BOT_USERNAME="{{BOT_USERNAME}}"
export AEO_BOT_PASSWORD="{{BOT_PASSWORD}}"
```

- [ ] **Step 6: Update `scripts/start-worker.sh`**

Require:

```bash
: "${AEO_CONTROLLER_URL:?AEO_CONTROLLER_URL is required}"
: "${AEO_BOT_USERNAME:?AEO_BOT_USERNAME is required}"
: "${AEO_BOT_PASSWORD:?AEO_BOT_PASSWORD is required}"
```

Remove any `AEO_TOKEN` requirement.

- [ ] **Step 7: Run worker/enroll tests**

Run:

```bash
cd backend && uv run pytest tests/api/test_enroll_api.py tests/worker -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/service/enroll_service.py backend/app/api/routes/enroll.py backend/app/worker/daemon.py scripts/start-worker.sh scripts/enroll.sh.tmpl backend/tests/api/test_enroll_api.py backend/tests/worker
git commit -m "feat: move workers to bot login"
```

## Task 9: Frontend Auth Foundation and Login Page

**Files:**
- Modify: `frontend/app/lib/api.ts`
- Create: `frontend/app/lib/auth.ts`
- Modify: `frontend/app/lib/types.ts`
- Create: `frontend/app/routes/login.tsx`
- Modify: `frontend/app/main.tsx`
- Modify: `frontend/app/root.tsx`

- [ ] **Step 1: Add auth types**

Modify `frontend/app/lib/types.ts`:

```ts
export type Principal = {
  username: string;
  source: "config" | "db" | "dev";
  groups: string[];
  permissions: string[];
};

export type LoginResponse = {
  accessToken: string;
  tokenType: "bearer";
  expiresAt: string;
  user: Principal;
};
```

- [ ] **Step 2: Rewrite API token handling**

Modify `frontend/app/lib/api.ts`:

```ts
const TOKEN_KEY = "aeo_access_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401) {
    clearToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.clone().json();
      detail = body.error ?? body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp;
}

export async function getJSON<T>(path: string): Promise<T> {
  const resp = await apiFetch(path);
  return (await resp.json()) as T;
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function del<T>(path: string): Promise<T> {
  const resp = await apiFetch(path, { method: "DELETE" });
  return (await resp.json()) as T;
}
```

- [ ] **Step 3: Add auth helpers**

Create `frontend/app/lib/auth.ts`:

```ts
import { getJSON, postJSON, setToken, clearToken } from "@/lib/api";
import type { LoginResponse, Principal } from "@/lib/types";

export async function login(username: string, password: string): Promise<LoginResponse> {
  const resp = await postJSON<LoginResponse>("/api/auth/login", { username, password });
  setToken(resp.accessToken);
  return resp;
}

export async function currentUser(): Promise<Principal> {
  return getJSON<Principal>("/api/auth/me");
}

export function logout(): void {
  clearToken();
  window.location.assign("/login");
}

export function hasPermission(user: Principal | undefined, permission: string): boolean {
  return Boolean(user?.permissions.includes(permission));
}
```

- [ ] **Step 4: Add login page**

Create `frontend/app/routes/login.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Button, Card, Input } from "@/components/ui";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="mx-auto flex min-h-screen max-w-sm items-center px-6">
        <Card className="w-full space-y-5">
          <div className="space-y-1">
            <h1 className="text-xl font-medium tracking-tight">Agent Eval Orchestrator</h1>
            <p className="text-sm text-muted-foreground">登录后继续</p>
          </div>
          <form className="space-y-3" onSubmit={onSubmit}>
            <Input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="用户名" autoComplete="username" />
            <Input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="密码" type="password" autoComplete="current-password" />
            <Button className="w-full" disabled={isSubmitting || !username || !password}>
              {isSubmitting ? "登录中" : "登录"}
            </Button>
          </form>
        </Card>
      </main>
    </div>
  );
}
```

- [ ] **Step 5: Add routes**

Modify `frontend/app/main.tsx`:

```tsx
import LoginPage from "./routes/login";

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <Root />,
    children: [
      { index: true, element: <TasksPage /> },
      { path: "create", element: <CreatePage /> },
      { path: "tasks/:runId", element: <TaskDetailPage /> },
      { path: "workers", element: <WorkersPage /> },
    ],
  },
]);
```

Remove `getToken()` import and the `?token=` persistence call.

- [ ] **Step 6: Guard root and add logout**

Modify `frontend/app/root.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { LogOut, Moon, Sun } from "lucide-react";
import { currentUser, hasPermission, logout } from "@/lib/auth";

const baseNavItems = [
  { to: "/", label: "任务", end: true, permission: "tasks.read_own" },
  { to: "/create", label: "新建任务", permission: "tasks.create" },
  { to: "/workers", label: "机器", permission: "workers.read" },
];
const adminNavItems = [
  { to: "/users", label: "用户", permission: "users.manage" },
  { to: "/groups", label: "组", permission: "groups.manage" },
];
```

Inside `Root()`:

```tsx
  const userQuery = useQuery({ queryKey: ["me"], queryFn: currentUser, retry: false });
  const user = userQuery.data;
  const navItems = [...baseNavItems, ...adminNavItems].filter((item) => hasPermission(user, item.permission));
```

Render a loading state while `userQuery.isLoading`, and render the username plus logout button in the header:

```tsx
<span className="ml-auto text-xs text-muted-foreground">{user?.username}</span>
<button type="button" onClick={logout} aria-label="退出登录" className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground">
  <LogOut className="size-4" />
</button>
```

- [ ] **Step 7: Run frontend build**

Run:

```bash
cd frontend && pnpm build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/lib/api.ts frontend/app/lib/auth.ts frontend/app/lib/types.ts frontend/app/routes/login.tsx frontend/app/main.tsx frontend/app/root.tsx
git commit -m "feat: add frontend login flow"
```

## Task 10: Frontend User and Group Management Pages

**Files:**
- Create: `frontend/app/routes/users.tsx`
- Create: `frontend/app/routes/groups.tsx`
- Modify: `frontend/app/main.tsx`
- Modify: `frontend/app/routes/create.tsx`
- Modify: `frontend/app/routes/workers.tsx`
- Modify: `frontend/app/lib/types.ts`

- [ ] **Step 1: Add management types**

Append to `frontend/app/lib/types.ts`:

```ts
export type UserRecord = {
  userId: string;
  username: string;
  displayName: string;
  isActive: boolean;
  groups: string[];
  createdAt: string;
  updatedAt: string;
  lastLoginAt: string | null;
};

export type GroupRecord = {
  groupId: string;
  name: string;
  displayName: string;
  description: string;
  isBuiltin: boolean;
  isActive: boolean;
  permissions: string[];
};

export type PermissionRecord = {
  code: string;
  description: string;
};
```

- [ ] **Step 2: Add users page**

Create `frontend/app/routes/users.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { del, getJSON, patchJSON, postJSON } from "@/lib/api";
import type { GroupRecord, UserRecord } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";

export default function UsersPage() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [group, setGroup] = useState("user");
  const users = useQuery({ queryKey: ["users"], queryFn: () => getJSON<{ users: UserRecord[] }>("/api/users") });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => getJSON<{ groups: GroupRecord[] }>("/api/groups") });
  const create = useMutation({
    mutationFn: () => postJSON<UserRecord>("/api/users", { username, displayName, password, groups: [group] }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      setPassword("");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const disable = useMutation({
    mutationFn: (id: string) => del(`/api/users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (error) => toast.error((error as Error).message),
  });
  const updateGroups = useMutation({
    mutationFn: ({ id, groups }: { id: string; groups: string[] }) => patchJSON<UserRecord>(`/api/users/${id}`, { groups }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (error) => toast.error((error as Error).message),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  const activeGroups = groups.data?.groups.filter((item) => item.isActive) ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-medium tracking-tight">用户管理</h1>
      </div>
      <Card>
        <form className="grid gap-3 md:grid-cols-5" onSubmit={onSubmit}>
          <Input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="用户名" />
          <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名" />
          <Input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="初始密码" type="password" />
          <select className="h-9 rounded-md border border-input bg-background px-3 text-sm" value={group} onChange={(event) => setGroup(event.target.value)}>
            {activeGroups.map((item) => <option key={item.name} value={item.name}>{item.displayName}</option>)}
          </select>
          <Button disabled={!username || !displayName || !password}>创建用户</Button>
        </form>
      </Card>
      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 h-11 font-medium">用户</th>
              <th className="px-4 h-11 font-medium">组</th>
              <th className="px-4 h-11 font-medium">状态</th>
              <th className="px-4 h-11 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {users.data?.users.map((user) => (
              <tr key={user.userId} className="border-t border-border">
                <td className="px-4 py-2">{user.displayName}<div className="text-xs text-muted-foreground">{user.username}</div></td>
                <td className="px-4 py-2">
                  <select className="h-8 rounded-md border border-input bg-background px-2 text-sm" value={user.groups[0] ?? ""} onChange={(event) => updateGroups.mutate({ id: user.userId, groups: [event.target.value] })}>
                    {activeGroups.map((item) => <option key={item.name} value={item.name}>{item.displayName}</option>)}
                  </select>
                </td>
                <td className="px-4 py-2"><Badge tone={user.isActive ? "green" : "red"}>{user.isActive ? "启用" : "禁用"}</Badge></td>
                <td className="px-4 py-2 text-right"><Button variant="danger" disabled={!user.isActive} onClick={() => disable.mutate(user.userId)}>禁用</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Add groups page**

Create `frontend/app/routes/groups.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { del, getJSON, postJSON, putJSON } from "@/lib/api";
import type { GroupRecord, PermissionRecord } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";

export default function GroupsPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => getJSON<{ groups: GroupRecord[] }>("/api/groups") });
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: () => getJSON<{ permissions: PermissionRecord[] }>("/api/permissions") });
  const create = useMutation({
    mutationFn: () => postJSON<GroupRecord>("/api/groups", { name, displayName, description }),
    onSuccess: () => {
      setName("");
      setDisplayName("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const disable = useMutation({
    mutationFn: (id: string) => del(`/api/groups/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });
  const setPermissions = useMutation({
    mutationFn: ({ id, permissions }: { id: string; permissions: string[] }) => putJSON<GroupRecord>(`/api/groups/${id}/permissions`, { permissions }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
    onError: (error) => toast.error((error as Error).message),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  function togglePermission(group: GroupRecord, code: string) {
    const next = group.permissions.includes(code)
      ? group.permissions.filter((item) => item !== code)
      : [...group.permissions, code];
    setPermissions.mutate({ id: group.groupId, permissions: next });
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium tracking-tight">组管理</h1>
      <Card>
        <form className="grid gap-3 md:grid-cols-4" onSubmit={onSubmit}>
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="组标识，例如 reviewer" />
          <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名" />
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述" />
          <Button disabled={!name || !displayName}>创建组</Button>
        </form>
      </Card>
      <div className="space-y-3">
        {groups.data?.groups.map((group) => (
          <Card key={group.groupId} className="space-y-3">
            <div className="flex items-center gap-3">
              <div>
                <div className="font-medium">{group.displayName}</div>
                <div className="text-xs text-muted-foreground">{group.name}</div>
              </div>
              {group.isBuiltin && <Badge tone="blue">内置</Badge>}
              {!group.isActive && <Badge tone="red">禁用</Badge>}
              <div className="ml-auto">
                <Button variant="danger" disabled={group.isBuiltin || !group.isActive} onClick={() => disable.mutate(group.groupId)}>禁用</Button>
              </div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {permissions.data?.permissions.map((permission) => (
                <label key={permission.code} className="flex items-start gap-2 border border-border p-2 text-sm">
                  <input type="checkbox" checked={group.permissions.includes(permission.code)} onChange={() => togglePermission(group, permission.code)} />
                  <span>
                    <span className="font-mono text-xs">{permission.code}</span>
                    <span className="block text-xs text-muted-foreground">{permission.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Register frontend routes**

Modify `frontend/app/main.tsx`:

```tsx
import GroupsPage from "./routes/groups";
import UsersPage from "./routes/users";

children: [
  { index: true, element: <TasksPage /> },
  { path: "create", element: <CreatePage /> },
  { path: "tasks/:runId", element: <TaskDetailPage /> },
  { path: "workers", element: <WorkersPage /> },
  { path: "users", element: <UsersPage /> },
  { path: "groups", element: <GroupsPage /> },
],
```

- [ ] **Step 5: Remove owner from create page payload**

Modify `frontend/app/routes/create.tsx` so the payload sent to `/api/eval-tasks/create-and-distribute` does not include `owner`. If the file has an `owner` state, remove that state and the input. The post body should be shaped like:

```tsx
{
  name,
  datasetPath,
  bitfunCliPath,
  bitfunConfigDir,
  selectedCaseIds,
  workerIds,
  perWorkerConcurrency,
  executorConfig,
  modelProfileRef,
}
```

- [ ] **Step 6: Hide worker management actions without permission**

Modify `frontend/app/routes/workers.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { currentUser, hasPermission } from "@/lib/auth";

const me = useQuery({ queryKey: ["me"], queryFn: currentUser });
const canManageWorkers = hasPermission(me.data, "workers.manage");
```

Render “添加机器”, enable/disable, and delete buttons only when `canManageWorkers` is true. Keep the table visible for users with `workers.read`.

- [ ] **Step 7: Run frontend build**

Run:

```bash
cd frontend && pnpm build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/lib/types.ts frontend/app/routes/users.tsx frontend/app/routes/groups.tsx frontend/app/main.tsx frontend/app/routes/create.tsx frontend/app/routes/workers.tsx
git commit -m "feat: add frontend user and group management"
```

## Task 11: Docs, Environment, and Full Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `backend/tests/api/test_templates_api.py`
- Modify: any remaining tests that assert `X-AEO-Token` success.

- [ ] **Step 1: Update `.env.example`**

Replace the shared token section with:

```dotenv
# 认证密钥:用于签名登录后的 Bearer token。生产环境必须换成随机长字符串。
AEO_AUTH_SECRET=change-me-random-secret

# 应急管理员:数据库不可用时仍可登录。不会写入数据库,也不会出现在用户管理页面。
AEO_ADMIN_USERNAME=admin
AEO_ADMIN_PASSWORD=change-me-admin-password

# worker/bot 用户:enroll 脚本和 worker daemon 用它登录后访问 worker 协议。
AEO_BOT_USERNAME=worker-bot
AEO_BOT_PASSWORD=change-me-bot-password

# 可选:访问 token 过期时间,单位分钟。
# AEO_ACCESS_TOKEN_TTL_MINUTES=480
```

Remove examples that tell browsers or workers to use `?token=` or `X-AEO-Token`.

- [ ] **Step 2: Update README auth section**

Replace the old auth section with:

```markdown
## 认证与权限

浏览器通过用户名/密码登录 `POST /api/auth/login`，后端返回 Bearer token。前端会把 token
保存在 localStorage，并通过 `Authorization: Bearer <token>` 访问 API。

首期内置三类组：

- `admin`：管理用户、组、机器和全部任务。
- `user`：创建并管理自己的任务，可查看基础机器状态。
- `bot`：worker 机器通信使用，只能访问 worker 协议和资产传输接口。

`.env` 可配置应急管理员和 bot 用户。配置用户不写入数据库，也不展示在用户管理页面。
```

Update worker enrollment docs:

```markdown
curl -fsSL "http://<controller>:8790/api/workers/enroll.sh" \
  -H "Authorization: Bearer <admin-token>" | bash
```

Explain that the generated script uses configured bot credentials to start the worker.

- [ ] **Step 3: Update old token tests**

Modify `backend/tests/api/test_templates_api.py::test_requires_token` to assert old token rejection:

```python
def test_old_shared_token_header_rejected(monkeypatch, session):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.setenv("AEO_AUTH_SECRET", "unit-secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    c = TestClient(app)
    assert c.get("/api/task-templates", headers={"X-AEO-Token": "secret"}).status_code == 401
    get_settings.cache_clear()
```

Use `rg -n "X-AEO-Token|\\?token=|AEO_TOKEN" backend/tests README.md .env.example frontend backend/app` to find remaining old success expectations. Keep `AEO_TOKEN` only as a deprecated setting if needed by config tests.

- [ ] **Step 4: Run backend tests**

Run:

```bash
cd backend && uv run pytest -v
```

Expected: PASS.

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd frontend && pnpm build
```

Expected: PASS.

- [ ] **Step 6: Run migration smoke test**

Run:

```bash
cd backend && DATABASE_URL=sqlite:////tmp/aeo_auth_rbac_plan_verify.db uv run alembic upgrade head
```

Expected: exits 0 and creates `/tmp/aeo_auth_rbac_plan_verify.db`.

- [ ] **Step 7: Commit**

```bash
git add .env.example README.md backend/tests backend/app frontend/app
git commit -m "docs: document user auth rbac"
```

## Self-Review

Spec coverage:

- DB users, soft delete, password hash: Tasks 1, 2, 5.
- Config admin and bot users outside DB: Tasks 3, 8, 11.
- Bearer token signed by `AEO_AUTH_SECRET`: Tasks 1, 3, 4.
- RBAC tables and built-in permissions: Tasks 2, 6.
- Group management in phase one: Tasks 6, 10.
- Permission-gated existing APIs and owner scope: Task 7.
- Worker/enroll migration away from global token: Task 8.
- Frontend login, nav, users, groups, worker button hiding, owner removal: Tasks 9 and 10.
- Docs, `.env.example`, full verification: Task 11.

Placeholder scan:

- No placeholder markers remain in this plan.
- Every route, schema, service, and test file named above has concrete code or an exact command.

Type consistency:

- Backend response schemas use snake_case fields and rely on existing `ApiModel` aliasing for camelCase JSON.
- Frontend types use camelCase fields matching API responses.
- Permission strings match `PermissionCode` values across backend tests, services, and frontend checks.
