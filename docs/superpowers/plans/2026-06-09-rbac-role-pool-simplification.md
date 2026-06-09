# 权限体系简化（角色 + 资源池）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 5 表 12 权限码的 RBAC 收敛为「3 个写死的系统角色 + 资源池」两维模型，并实现“按池划分机器、池内用户只能调度到池内机器”。

**Architecture:** 单张 `roles` 表用 `type` 区分 `system`(admin/user/bot，写死) 与 `pool`(管理员创建)；`user_roles` 多对多绑定；`workers.role_id` / `runs.role_id` 指向 pool 角色。鉴权由权限码改为按系统角色判断（`require_role`），任务调度按 run 的 pool 过滤候选机器。账号来源、Bearer token、配置用户应急登录沿用现有机制。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 / Alembic / pytest（后端）；React 19 / Vite / TypeScript / TanStack Query（前端）。

**Spec:** `docs/superpowers/specs/2026-06-09-rbac-role-pool-simplification-design.md`

**设计微调（相对 spec §6/§8）：** worker 的 pool 归属由管理员在机器页指派（`PATCH /api/workers/{id}` 写 `role_id`），不在 enroll 脚本里写死。`upsert_worker` 不覆盖 `role_id`，重注册可保留。enroll `--pool` 作为后续可选扩展，不在本计划范围。

---

## 文件结构

**后端新增/改造：**
- `backend/app/core/roles.py`（新增，取代 `core/permissions.py`）：系统角色常量与种子元数据。
- `backend/app/model/tables.py`：新增 `Role`/`UserRole`，删 `Group`/`Permission`/`UserGroup`/`GroupPermission`，`Worker`/`Run` 加 `role_id`。
- `backend/alembic/versions/0003_role_pool.py`（新增）：迁移。
- `backend/app/model/repo_auth.py`：重写为 role/pool 仓储。
- `backend/app/core/security.py`：token payload `groups`+`permissions` → `role`+`pools`。
- `backend/app/service/auth_service.py`：`Principal` 改 `role`+`pools`。
- `backend/app/api/deps.py`：`require_permission` → `require_role`。
- `backend/app/service/rbac_service.py`：用户改 role+pools；新增 pool 服务。
- `backend/app/schema/users.py` / `schema/auth.py` / `schema/pools.py`（新增）/ 删 `schema/groups.py`。
- `backend/app/api/routes/users.py` / `pools.py`(新增) / `workers.py` / `runs.py` / `worker_protocol.py` / `enroll.py` / 删 `routes/groups.py` / `api/router.py`。
- `backend/app/service/run_service.py`：create 绑定 pool。
- `backend/app/service/orchestration/scheduler.py`：按 pool 过滤。

**前端：**
- `frontend/app/root.tsx`：导航按 role 过滤。
- `frontend/app/routes/users.tsx`：role 单选 + pools 多选。
- `frontend/app/routes/workers.tsx`：资源池管理 + 机器指派池。
- `frontend/app/routes/create.tsx`：资源池下拉。
- `frontend/app/routes/groups.tsx`（删除）+ `app/main.tsx` 去路由。

**测试：** 沿用 `backend/tests/...` 结构。删除/改写 `tests/api/test_groups_api.py`、`tests/model/test_repo_auth.py`、`tests/service/test_auth_service.py`、`tests/api/test_rbac_existing_routes.py`、`tests/api/test_users_api.py`、`tests/orchestration/test_scheduler.py`。

---

## Phase A — 数据模型与迁移

### Task 1: 系统角色常量模块 `core/roles.py`

**Files:**
- Create: `backend/app/core/roles.py`
- Test: `backend/tests/core/test_roles.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/core/test_roles.py
from app.core.roles import SystemRole, SYSTEM_ROLE_META


def test_system_roles_are_exactly_three():
    assert SystemRole.all() == ["admin", "user", "bot"]


def test_system_role_meta_covers_all():
    assert set(SYSTEM_ROLE_META) == set(SystemRole.all())
    assert SYSTEM_ROLE_META["admin"]["display_name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/core/test_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: app.core.roles`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/core/roles.py
from __future__ import annotations

ROLE_TYPE_SYSTEM = "system"
ROLE_TYPE_POOL = "pool"


class SystemRole:
    ADMIN = "admin"
    USER = "user"
    BOT = "bot"

    @classmethod
    def all(cls) -> list[str]:
        return [cls.ADMIN, cls.USER, cls.BOT]


SYSTEM_ROLE_META: dict[str, dict[str, str]] = {
    SystemRole.ADMIN: {"display_name": "管理员", "description": "拥有系统全部权限"},
    SystemRole.USER: {"display_name": "普通用户", "description": "创建和管理自己的评测任务"},
    SystemRole.BOT: {"display_name": "机器账号", "description": "worker 机器通信使用"},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/core/test_roles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/roles.py backend/tests/core/test_roles.py
git commit -m "feat: add system role constants module"
```

---

### Task 2: ORM 表 `Role` / `UserRole`，删旧表，加 role_id

**Files:**
- Modify: `backend/app/model/tables.py`

- [ ] **Step 1: 替换 auth 相关表定义**

把 `tables.py` 第 12-58 行（`User` 之后的 `Group`/`Permission`/`UserGroup`/`GroupPermission`）替换为：保留 `User` 原样，删除 `Group`/`Permission`/`UserGroup`/`GroupPermission`，新增 `Role`/`UserRole`。

```python
class Role(Base):
    __tablename__ = "roles"
    role_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # "system" | "pool"
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
```

- [ ] **Step 2: 给 `Worker` 加 `role_id`**

在 `Worker` 类的 `tags` 字段后加：

```python
    role_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
```

- [ ] **Step 3: 给 `Run` 加 `role_id`**

在 `Run` 类的 `owner` 字段后加：

```python
    role_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: 验证导入不报错**

Run: `cd backend && uv run python -c "import app.model.tables as t; print(t.Role, t.UserRole)"`
Expected: 打印两个类，无 `ImportError`/`AttributeError`

- [ ] **Step 5: Commit**

```bash
git add backend/app/model/tables.py
git commit -m "feat: replace group tables with role tables, add role_id"
```

---

### Task 3: Alembic 迁移 0003

**Files:**
- Create: `backend/alembic/versions/0003_role_pool.py`

- [ ] **Step 1: 写迁移**

`down_revision` 指向 `0002`（确认：`grep "revision" backend/alembic/versions/0002_auth_rbac.py`）。

```python
# backend/alembic/versions/0003_role_pool.py
"""role + pool model

Revision ID: 0003_role_pool
Revises: 0002_auth_rbac
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_role_pool"
down_revision = "0002_auth_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("role_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)
    op.create_index("ix_roles_type", "roles", ["type"])
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role_id", sa.String(), nullable=False),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])
    op.add_column("workers", sa.Column("role_id", sa.String(), nullable=True))
    op.create_index("ix_workers_role_id", "workers", ["role_id"])
    op.add_column("runs", sa.Column("role_id", sa.String(), nullable=True))

    op.drop_table("group_permissions")
    op.drop_table("user_groups")
    op.drop_table("permissions")
    op.drop_table("groups")


def downgrade() -> None:
    raise NotImplementedError("0003 is not reversible")
```

- [ ] **Step 2: 在临时库上验证迁移可跑**

Run: `cd backend && AEO_DATABASE_URL="sqlite:///$(mktemp -u).db" uv run alembic upgrade head`
Expected: 输出 `Running upgrade 0002_auth_rbac -> 0003_role_pool`，无异常。
（若项目用别的库 env 名，按 `backend/alembic/env.py` 实际变量调整。）

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/0003_role_pool.py
git commit -m "feat: migrate to role + pool schema"
```

---

### Task 4: 重写 `repo_auth.py`

**Files:**
- Modify: `backend/app/model/repo_auth.py`
- Test: `backend/tests/model/test_repo_auth.py`（整体改写）

- [ ] **Step 1: 改写测试**

```python
# backend/tests/model/test_repo_auth.py
import pytest

from app.core.roles import SystemRole
from app.core.security import hash_password
from app.model import repo_auth


def test_bootstrap_creates_system_roles(session):
    repo_auth.bootstrap_roles(session)
    roles = {r.name: r for r in repo_auth.list_roles(session)}
    for name in SystemRole.all():
        assert roles[name].type == "system"


def test_bootstrap_is_idempotent(session):
    repo_auth.bootstrap_roles(session)
    repo_auth.bootstrap_roles(session)
    system = [r for r in repo_auth.list_roles(session) if r.type == "system"]
    assert len(system) == 3


def test_create_user_with_role_and_pools(session):
    repo_auth.bootstrap_roles(session)
    repo_auth.create_pool(session, name="pool-a", display_name="A 池")
    user = repo_auth.create_user(
        session, username="alice", display_name="Alice",
        password_hash=hash_password("secret"),
        system_role=SystemRole.USER, pool_names=["pool-a"],
    )
    session.commit()
    assert repo_auth.system_role_for_user(session, user.user_id) == SystemRole.USER
    assert repo_auth.pool_names_for_user(session, user.user_id) == ["pool-a"]


def test_set_user_roles_requires_known_pool(session):
    repo_auth.bootstrap_roles(session)
    user = repo_auth.create_user(
        session, username="bob", display_name="Bob",
        password_hash=hash_password("x"), system_role=SystemRole.USER, pool_names=[],
    )
    session.commit()
    with pytest.raises(ValueError):
        repo_auth.set_user_roles(session, user.user_id, system_role=SystemRole.USER, pool_names=["ghost"])


def test_set_user_roles_rejects_unknown_system_role(session):
    repo_auth.bootstrap_roles(session)
    user = repo_auth.create_user(
        session, username="cara", display_name="Cara",
        password_hash=hash_password("x"), system_role=SystemRole.USER, pool_names=[],
    )
    session.commit()
    with pytest.raises(ValueError):
        repo_auth.set_user_roles(session, user.user_id, system_role="superuser", pool_names=[])
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/model/test_repo_auth.py -v`
Expected: FAIL（`bootstrap_roles`/`create_pool` 等未定义）

- [ ] **Step 3: 重写 `repo_auth.py`**

```python
# backend/app/model/repo_auth.py
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.core.roles import ROLE_TYPE_POOL, ROLE_TYPE_SYSTEM, SYSTEM_ROLE_META, SystemRole
from app.model.tables import Role, User, UserRole


def bootstrap_roles(session: Session) -> None:
    existing = {r.name for r in session.scalars(select(Role)).all()}
    for name in SystemRole.all():
        if name not in existing:
            session.add(Role(
                role_id=new_id("role"), name=name, type=ROLE_TYPE_SYSTEM,
                display_name=SYSTEM_ROLE_META[name]["display_name"],
            ))
    session.flush()


# ---- roles / pools ----

def list_roles(session: Session, *, type: str | None = None) -> list[Role]:
    stmt = select(Role).order_by(Role.type, Role.name)
    if type is not None:
        stmt = stmt.where(Role.type == type)
    return list(session.scalars(stmt).all())


def get_role(session: Session, role_id: str) -> Role | None:
    return session.get(Role, role_id)


def get_role_by_name(session: Session, name: str) -> Role | None:
    return session.scalar(select(Role).where(Role.name == name))


def create_pool(session: Session, *, name: str, display_name: str) -> Role:
    role = Role(role_id=new_id("role"), name=name, type=ROLE_TYPE_POOL, display_name=display_name)
    session.add(role)
    session.flush()
    return role


def delete_pool(session: Session, role_id: str) -> None:
    session.execute(delete(UserRole).where(UserRole.role_id == role_id))
    session.execute(delete(Role).where(Role.role_id == role_id))
    session.flush()


# ---- users ----

def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def list_users(session: Session, *, include_inactive: bool = True) -> list[User]:
    stmt = select(User).order_by(User.username)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    return list(session.scalars(stmt).all())


def create_user(session: Session, *, username: str, display_name: str, password_hash: str,
                system_role: str, pool_names: list[str]) -> User:
    user = User(user_id=new_id("usr"), username=username, display_name=display_name,
                password_hash=password_hash, is_active=True)
    session.add(user)
    session.flush()
    set_user_roles(session, user.user_id, system_role=system_role, pool_names=pool_names)
    return user


def set_user_roles(session: Session, user_id: str, *, system_role: str, pool_names: list[str]) -> None:
    if system_role not in SystemRole.all():
        raise ValueError(f"unknown system role: {system_role}")
    pools = list(session.scalars(
        select(Role).where(Role.name.in_(pool_names), Role.type == ROLE_TYPE_POOL)
    ).all())
    found = {p.name for p in pools}
    missing = sorted(set(pool_names) - found)
    if missing:
        raise ValueError(f"unknown pools: {', '.join(missing)}")
    sys_role = get_role_by_name(session, system_role)
    assert sys_role is not None
    session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    session.add(UserRole(user_id=user_id, role_id=sys_role.role_id))
    for pool in pools:
        session.add(UserRole(user_id=user_id, role_id=pool.role_id))
    session.flush()


def system_role_for_user(session: Session, user_id: str) -> str | None:
    stmt = (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user_id, Role.type == ROLE_TYPE_SYSTEM)
    )
    return session.scalar(stmt)


def pool_names_for_user(session: Session, user_id: str) -> list[str]:
    stmt = (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user_id, Role.type == ROLE_TYPE_POOL)
        .order_by(Role.name)
    )
    return list(session.scalars(stmt).all())


def count_pool_members(session: Session, role_id: str) -> int:
    return len(list(session.scalars(select(UserRole.id).where(UserRole.role_id == role_id)).all()))


def touch_user_login(session: Session, user_id: str) -> None:
    user = get_user(session, user_id)
    if user is not None:
        user.last_login_at = now_iso()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/model/test_repo_auth.py -v`
Expected: PASS（5 个用例）

- [ ] **Step 5: Commit**

```bash
git add backend/app/model/repo_auth.py backend/tests/model/test_repo_auth.py
git commit -m "feat: rewrite repo_auth for roles and pools"
```

---

## Phase B — 后端认证与服务层

### Task 5: token payload 改 role + pools

**Files:**
- Modify: `backend/app/core/security.py`
- Test: `backend/tests/core/test_security_token.py`（新增）

- [ ] **Step 1: Write failing test**

```python
# backend/tests/core/test_security_token.py
from app.core.security import create_access_token, verify_access_token


def test_token_roundtrip_carries_role_and_pools():
    token = create_access_token(
        subject="alice", source="db", role="user", pools=["pool-a"],
        secret="s3cret", ttl_seconds=60,
    )
    payload = verify_access_token(token, secret="s3cret")
    assert payload.subject == "alice"
    assert payload.role == "user"
    assert payload.pools == ["pool-a"]
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/core/test_security_token.py -v`
Expected: FAIL（`create_access_token` 仍要求 `groups`/`permissions`）

- [ ] **Step 3: 改 `security.py`**

`TokenPayload`：把 `groups: list[str]` / `permissions: list[str]` 两字段替换为 `role: str` / `pools: list[str]`。

`create_access_token` 签名改为：

```python
def create_access_token(
    *,
    subject: str,
    source: Literal["config", "db", "dev"],
    role: str,
    pools: list[str],
    secret: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": subject, "source": source, "role": role, "pools": pools,
        "iat": int(now.timestamp()), "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
```

`verify_access_token` 的校验块改为：

```python
    subject = payload.get("sub")
    source = payload.get("source")
    role = payload.get("role")
    pools = payload.get("pools")
    exp = payload.get("exp")
    if (
        not isinstance(subject, str)
        or source not in {"config", "db", "dev"}
        or not isinstance(role, str)
        or not isinstance(pools, list)
        or not all(isinstance(item, str) for item in pools)
        or not isinstance(exp, int)
    ):
        raise InvalidTokenError("malformed access token")
    return TokenPayload(
        subject=subject, source=source, role=role, pools=list(pools),
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/core/test_security_token.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/security.py backend/tests/core/test_security_token.py
git commit -m "feat: carry role and pools in access token"
```

---

### Task 6: `auth_service.Principal` 改 role + pools

**Files:**
- Modify: `backend/app/service/auth_service.py`
- Test: `backend/tests/service/test_auth_service.py`（改写）

- [ ] **Step 1: 改写测试**

```python
# backend/tests/service/test_auth_service.py
from app.core.roles import SystemRole
from app.core.security import hash_password
from app.model import repo_auth
from app.service import auth_service


def test_db_user_authenticates_with_role_and_pools(session, monkeypatch):
    repo_auth.bootstrap_roles(session)
    repo_auth.create_pool(session, name="pool-a", display_name="A")
    repo_auth.create_user(
        session, username="alice", display_name="Alice",
        password_hash=hash_password("secret"), system_role=SystemRole.USER, pool_names=["pool-a"],
    )
    session.commit()
    principal = auth_service.authenticate(session, "alice", "secret")
    assert principal is not None
    assert principal.role == SystemRole.USER
    assert principal.pools == ["pool-a"]


def test_config_admin_authenticates(monkeypatch, session):
    from app.core.config import get_settings
    monkeypatch.setenv("AEO_ADMIN_USERNAME", "root")
    monkeypatch.setenv("AEO_ADMIN_PASSWORD", "pw")
    get_settings.cache_clear()
    principal = auth_service.authenticate(session, "root", "pw")
    assert principal is not None and principal.role == SystemRole.ADMIN and principal.source == "config"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/service/test_auth_service.py -v`
Expected: FAIL

- [ ] **Step 3: 改写 `auth_service.py`**

```python
# backend/app/service/auth_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.roles import SystemRole
from app.core.security import create_access_token, verify_password
from app.model import repo_auth


@dataclass(frozen=True)
class Principal:
    username: str
    source: str
    role: str
    pools: list[str]

    @property
    def is_admin(self) -> bool:
        return self.role == SystemRole.ADMIN


def _config_principal(username: str, role: str) -> Principal:
    return Principal(username=username, source="config", role=role, pools=[])


def authenticate_config_user(username: str, password: str) -> Principal | None:
    settings = get_settings()
    if settings.admin_username and settings.admin_password:
        if username == settings.admin_username and password == settings.admin_password:
            return _config_principal(username, SystemRole.ADMIN)
    if settings.bot_username and settings.bot_password:
        if username == settings.bot_username and password == settings.bot_password:
            return _config_principal(username, SystemRole.BOT)
    return None


def authenticate_db_user(session: Session, username: str, password: str) -> Principal | None:
    user = repo_auth.get_user_by_username(session, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    role = repo_auth.system_role_for_user(session, user.user_id) or SystemRole.USER
    pools = repo_auth.pool_names_for_user(session, user.user_id)
    repo_auth.touch_user_login(session, user.user_id)
    session.commit()
    return Principal(username=user.username, source="db", role=role, pools=pools)


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
        subject=principal.username, source=principal.source,  # type: ignore[arg-type]
        role=principal.role, pools=principal.pools,
        secret=settings.auth_secret, ttl_seconds=ttl_seconds,
    )
    return token, expires_at


def dev_principal() -> Principal:
    return Principal(username="dev", source="dev", role=SystemRole.ADMIN, pools=[])
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/service/test_auth_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/service/auth_service.py backend/tests/service/test_auth_service.py
git commit -m "feat: principal carries role and pools"
```

---

### Task 7: `deps.py` 改 `require_role`

**Files:**
- Modify: `backend/app/api/deps.py`

- [ ] **Step 1: 改 `require_current_principal` 与守卫**

把 `require_current_principal` 末尾构造 Principal 改为：

```python
    return Principal(
        username=payload.subject,
        source=payload.source,
        role=payload.role,
        pools=payload.pools,
    )
```

删除 `require_permission`，新增：

```python
def require_role(*roles: str) -> Callable[[Principal], Principal]:
    allowed = set(roles)

    def dependency(principal: Principal = Depends(require_current_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role not allowed: {principal.role}",
            )
        return principal

    return dependency
```

- [ ] **Step 2: 验证导入**

Run: `cd backend && uv run python -c "from app.api.deps import require_role, require_current_principal"`
Expected: 无报错

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/deps.py
git commit -m "feat: replace require_permission with require_role"
```

---

### Task 8: 重写 `rbac_service.py`（用户 + pool 服务）

**Files:**
- Modify: `backend/app/service/rbac_service.py`
- Test: `backend/tests/service/test_rbac_service.py`（新增）

- [ ] **Step 1: Write failing test**

```python
# backend/tests/service/test_rbac_service.py
import pytest

from app.core.roles import SystemRole
from app.model import repo_auth
from app.service import rbac_service
from app.service.errors import ServiceError


def test_create_user_returns_role_and_pools(session):
    repo_auth.bootstrap_roles(session)
    rbac_service.create_pool(session, name="pool-a", display_name="A 池")
    out = rbac_service.create_user(
        session, username="alice", display_name="Alice", password="secret",
        role=SystemRole.USER, pools=["pool-a"],
    )
    assert out["role"] == SystemRole.USER
    assert out["pools"] == ["pool-a"]


def test_delete_pool_in_use_is_rejected(session):
    repo_auth.bootstrap_roles(session)
    pool = rbac_service.create_pool(session, name="pool-a", display_name="A 池")
    rbac_service.create_user(
        session, username="bob", display_name="Bob", password="x",
        role=SystemRole.USER, pools=["pool-a"],
    )
    with pytest.raises(ServiceError):
        rbac_service.delete_pool(session, pool["role_id"])
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/service/test_rbac_service.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 `rbac_service.py`**

```python
# backend/app/service/rbac_service.py
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.core.roles import ROLE_TYPE_POOL
from app.core.security import hash_password
from app.model import repo_auth
from app.model.tables import Role, User, Worker
from app.service.errors import ConflictError, NotFoundError, ServiceError


# ---- users ----

def user_to_read(session: Session, user: User) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "is_active": bool(user.is_active),
        "role": repo_auth.system_role_for_user(session, user.user_id),
        "pools": repo_auth.pool_names_for_user(session, user.user_id),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login_at": user.last_login_at,
    }


def list_users(session: Session) -> list[dict]:
    return [user_to_read(session, u) for u in repo_auth.list_users(session)]


def create_user(session: Session, *, username: str, display_name: str, password: str,
                role: str, pools: list[str]) -> dict:
    if repo_auth.get_user_by_username(session, username) is not None:
        raise ConflictError(f"user already exists: {username}")
    try:
        user = repo_auth.create_user(
            session, username=username, display_name=display_name,
            password_hash=hash_password(password), system_role=role, pool_names=pools,
        )
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    session.commit()
    return user_to_read(session, user)


def get_user_read(session: Session, user_id: str) -> dict:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    return user_to_read(session, user)


def update_user(session: Session, user_id: str, *, display_name: str | None,
                is_active: bool | None, role: str | None, pools: list[str] | None) -> dict:
    user = repo_auth.get_user(session, user_id)
    if user is None:
        raise NotFoundError(f"user not found: {user_id}")
    if display_name is not None:
        user.display_name = display_name
    if is_active is not None:
        user.is_active = is_active
    if role is not None or pools is not None:
        new_role = role if role is not None else repo_auth.system_role_for_user(session, user_id)
        new_pools = pools if pools is not None else repo_auth.pool_names_for_user(session, user_id)
        try:
            repo_auth.set_user_roles(session, user_id, system_role=new_role, pool_names=new_pools)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
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


# ---- pools ----

def pool_to_read(session: Session, role: Role) -> dict:
    worker_count = len(list(session.scalars(select(Worker.worker_id).where(Worker.role_id == role.role_id)).all()))
    return {
        "role_id": role.role_id,
        "name": role.name,
        "display_name": role.display_name,
        "member_count": repo_auth.count_pool_members(session, role.role_id),
        "worker_count": worker_count,
    }


def list_pools(session: Session) -> list[dict]:
    return [pool_to_read(session, r) for r in repo_auth.list_roles(session, type=ROLE_TYPE_POOL)]


def create_pool(session: Session, *, name: str, display_name: str) -> dict:
    if repo_auth.get_role_by_name(session, name) is not None:
        raise ConflictError(f"role name already exists: {name}")
    role = repo_auth.create_pool(session, name=name, display_name=display_name)
    session.commit()
    return pool_to_read(session, role)


def update_pool(session: Session, role_id: str, *, display_name: str | None) -> dict:
    role = repo_auth.get_role(session, role_id)
    if role is None or role.type != ROLE_TYPE_POOL:
        raise NotFoundError(f"pool not found: {role_id}")
    if display_name is not None:
        role.display_name = display_name
    session.commit()
    return pool_to_read(session, role)


def delete_pool(session: Session, role_id: str) -> None:
    role = repo_auth.get_role(session, role_id)
    if role is None or role.type != ROLE_TYPE_POOL:
        raise NotFoundError(f"pool not found: {role_id}")
    worker_count = len(list(session.scalars(select(Worker.worker_id).where(Worker.role_id == role_id)).all()))
    if worker_count > 0:
        raise ServiceError("pool still has workers; reassign them first")
    if repo_auth.count_pool_members(session, role_id) > 0:
        raise ServiceError("pool still has members; remove them first")
    repo_auth.delete_pool(session, role_id)
    session.commit()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/service/test_rbac_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/service/rbac_service.py backend/tests/service/test_rbac_service.py
git commit -m "feat: rewrite rbac_service for users and pools"
```

---

### Task 9: schema 调整（users / auth / pools / 删 groups）

**Files:**
- Modify: `backend/app/schema/users.py`, `backend/app/schema/auth.py`
- Create: `backend/app/schema/pools.py`
- Delete: `backend/app/schema/groups.py`

- [ ] **Step 1: 改 `schema/users.py`**

```python
from app.schema.common import ApiModel


class UserCreate(ApiModel):
    username: str
    display_name: str
    password: str
    role: str
    pools: list[str] = []


class UserUpdate(ApiModel):
    display_name: str | None = None
    is_active: bool | None = None
    role: str | None = None
    pools: list[str] | None = None


class PasswordReset(ApiModel):
    password: str


class UserRead(ApiModel):
    user_id: str
    username: str
    display_name: str
    is_active: bool
    role: str | None = None
    pools: list[str] = []
    created_at: str
    updated_at: str
    last_login_at: str | None = None
```

- [ ] **Step 2: 改 `schema/auth.py` 的 `PrincipalRead`**

把 `groups`/`permissions` 两字段替换为：

```python
class PrincipalRead(ApiModel):
    username: str
    source: str
    role: str
    pools: list[str] = []
```

- [ ] **Step 3: 新增 `schema/pools.py`**

```python
from app.schema.common import ApiModel


class PoolCreate(ApiModel):
    name: str
    display_name: str


class PoolUpdate(ApiModel):
    display_name: str | None = None


class PoolRead(ApiModel):
    role_id: str
    name: str
    display_name: str
    member_count: int
    worker_count: int
```

- [ ] **Step 4: 删除 `schema/groups.py`**

```bash
git rm backend/app/schema/groups.py
```

- [ ] **Step 5: 验证导入**

Run: `cd backend && uv run python -c "import app.schema.users, app.schema.auth, app.schema.pools"`
Expected: 无报错

- [ ] **Step 6: Commit**

```bash
git add backend/app/schema/
git commit -m "feat: role/pool schemas, drop group schema"
```

---

## Phase C — 后端路由与编排

### Task 10: `users` 路由改 `require_role("admin")` + role/pools

**Files:**
- Modify: `backend/app/api/routes/users.py`
- Test: `backend/tests/api/test_users_api.py`（改写关键断言）

- [ ] **Step 1: 改写测试核心用例**

```python
# backend/tests/api/test_users_api.py（替换创建相关用例）
from app.model import repo_auth


def test_create_user_with_role_and_pools(client, session):
    repo_auth.bootstrap_roles(session)
    repo_auth.create_pool(session, name="pool-a", display_name="A 池")
    session.commit()
    resp = client.post("/api/users", json={
        "username": "alice", "displayName": "Alice", "password": "secret",
        "role": "user", "pools": ["pool-a"],
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "user"
    assert body["pools"] == ["pool-a"]
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/api/test_users_api.py::test_create_user_with_role_and_pools -v`
Expected: FAIL

- [ ] **Step 3: 改 `routes/users.py`**

顶部 import 改为：

```python
from app.api.deps import db_session, require_role
from app.core.roles import SystemRole
from app.schema.users import PasswordReset, UserCreate, UserRead, UserUpdate
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_role(SystemRole.ADMIN))])
```

`create_user` body 透传改为 `role=body.role, pools=body.pools`；`update_user` 改为 `role=body.role, pools=body.pools`（去掉 `groups=`）。

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/api/test_users_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/users.py backend/tests/api/test_users_api.py
git commit -m "feat: users route uses role and pools"
```

---

### Task 11: 新增 `pools` 路由 + workers 指派池 + 删 groups 路由 + router 接线

**Files:**
- Create: `backend/app/api/routes/pools.py`
- Modify: `backend/app/api/routes/workers.py`, `backend/app/api/router.py`
- Delete: `backend/app/api/routes/groups.py`
- Test: `backend/tests/api/test_pools_api.py`（新增）

- [ ] **Step 1: Write failing test**

```python
# backend/tests/api/test_pools_api.py
from app.model import repo_auth


def test_pool_crud_and_assign_worker(client, session):
    repo_auth.bootstrap_roles(session)
    session.commit()
    created = client.post("/api/pools", json={"name": "pool-a", "displayName": "A 池"})
    assert created.status_code == 201, created.text
    role_id = created.json()["roleId"]

    listing = client.get("/api/pools").json()["pools"]
    assert any(p["roleId"] == role_id for p in listing)

    # assign a worker to the pool
    from app.model import repo_workers
    repo_workers.upsert_worker(session, worker_id="w1", display_name="w1", host="h",
                               slots_total=1, capabilities={})
    session.commit()
    assigned = client.patch("/api/workers/w1", json={"roleId": role_id})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["roleId"] == role_id
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/api/test_pools_api.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 新增 `routes/pools.py`**

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_role
from app.core.roles import SystemRole
from app.schema.pools import PoolCreate, PoolRead, PoolUpdate
from app.service import rbac_service

router = APIRouter(dependencies=[Depends(require_role(SystemRole.ADMIN))])


@router.get("/pools")
def list_pools(session: Session = Depends(db_session)) -> dict:
    return {"pools": [PoolRead.model_validate(p).model_dump(by_alias=True) for p in rbac_service.list_pools(session)]}


@router.post("/pools", response_model=PoolRead, status_code=status.HTTP_201_CREATED)
def create_pool(body: PoolCreate, session: Session = Depends(db_session)) -> PoolRead:
    return PoolRead.model_validate(rbac_service.create_pool(session, name=body.name, display_name=body.display_name))


@router.patch("/pools/{role_id}", response_model=PoolRead)
def update_pool(role_id: str, body: PoolUpdate, session: Session = Depends(db_session)) -> PoolRead:
    return PoolRead.model_validate(rbac_service.update_pool(session, role_id, display_name=body.display_name))


@router.delete("/pools/{role_id}")
def delete_pool(role_id: str, session: Session = Depends(db_session)) -> dict:
    rbac_service.delete_pool(session, role_id)
    return {"ok": True}
```

- [ ] **Step 4: workers 路由加 PATCH 指派池**

`routes/workers.py` 顶部 import 改 `from app.api.deps import db_session, require_role` + `from app.core.roles import SystemRole`；把三处 `require_permission(PermissionCode.WORKERS_READ/MANAGE)` 改为 `require_role(SystemRole.ADMIN)`（普通用户不再访问机器页）。新增：

```python
from app.schema.workers import WorkerAssignPool  # 见下

@router.patch("/workers/{worker_id}", dependencies=[Depends(require_role(SystemRole.ADMIN))])
def assign_pool(worker_id: str, body: WorkerAssignPool, session: Session = Depends(db_session)) -> dict:
    from app.model import repo_auth, repo_workers
    worker = repo_workers.get_worker(session, worker_id)
    if worker is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="worker not found")
    if body.role_id is not None:
        role = repo_auth.get_role(session, body.role_id)
        if role is None or role.type != "pool":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="invalid pool")
    worker.role_id = body.role_id
    session.commit()
    return {"workerId": worker.worker_id, "roleId": worker.role_id}
```

在 `backend/app/schema/workers.py` 末尾加：

```python
class WorkerAssignPool(ApiModel):
    role_id: str | None = None
```

（确认 `schema/workers.py` 顶部已 `from app.schema.common import ApiModel`；若无则加。）

- [ ] **Step 5: 删 groups 路由 + 改 `router.py`**

```bash
git rm backend/app/api/routes/groups.py
```

`api/router.py`：从 import 列表去掉 `groups`，去掉 `authed_router.include_router(groups.router, ...)`，新增 `from app.api.routes import ... pools ...` 与 `authed_router.include_router(pools.router, tags=["pools"])`。

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && uv run pytest tests/api/test_pools_api.py -v`
Expected: PASS

- [ ] **Step 7: 删 groups 测试**

```bash
git rm backend/tests/api/test_groups_api.py
```

- [ ] **Step 8: Commit**

```bash
git add -A backend/app/api backend/app/schema/workers.py backend/tests/api/test_pools_api.py
git commit -m "feat: add pools routes and worker pool assignment, drop groups route"
```

---

### Task 12: `runs` 路由守卫 + 创建绑定 pool + owner 隔离

**Files:**
- Modify: `backend/app/api/routes/runs.py`, `backend/app/service/run_service.py`, `backend/app/schema/runs.py`
- Test: `backend/tests/api/test_runs_pool.py`（新增）

- [ ] **Step 1: Write failing test**

```python
# backend/tests/api/test_runs_pool.py
from app.service import run_service


def test_resolve_pool_user_single_pool_defaults(monkeypatch):
    # user with exactly one pool and no explicit choice -> that pool
    role_id = run_service.resolve_pool_role(role="user", user_pools=["pool-a"], requested=None,
                                            name_to_id={"pool-a": "role-a"})
    assert role_id == "role-a"


def test_resolve_pool_user_no_pool_raises():
    import pytest
    from app.service.errors import ServiceError
    with pytest.raises(ServiceError):
        run_service.resolve_pool_role(role="user", user_pools=[], requested=None, name_to_id={})


def test_resolve_pool_admin_optional():
    assert run_service.resolve_pool_role(role="admin", user_pools=[], requested=None, name_to_id={}) is None


def test_resolve_pool_user_must_belong():
    import pytest
    from app.service.errors import ServiceError
    with pytest.raises(ServiceError):
        run_service.resolve_pool_role(role="user", user_pools=["pool-a"], requested="pool-b",
                                      name_to_id={"pool-a": "role-a", "pool-b": "role-b"})
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/api/test_runs_pool.py -v`
Expected: FAIL（`resolve_pool_role` 未定义）

- [ ] **Step 3: 在 `run_service.py` 加纯函数 + 绑定**

```python
# run_service.py 顶部 import
from app.service.errors import ServiceError

def resolve_pool_role(*, role: str, user_pools: list[str], requested: str | None,
                      name_to_id: dict[str, str]) -> str | None:
    """返回任务要绑定的 pool role_id；admin 可为 None（不限）。"""
    if role == "admin":
        if requested:
            return name_to_id.get(requested)
        return None
    # 普通用户
    if requested is not None:
        if requested not in user_pools:
            raise ServiceError(f"not a member of pool: {requested}")
        return name_to_id[requested]
    if len(user_pools) == 1:
        return name_to_id[user_pools[0]]
    if not user_pools:
        raise ServiceError("no pool assigned; contact an administrator")
    raise ServiceError("multiple pools; pool selection required")
```

`create_and_distribute` 签名增加 `role_id: str | None = None`，并在 `repo_runs.create_run(...)` 调用处传 `role_id=role_id`（确认 `repo_runs.create_run` 接受 `role_id`，否则在该 repo 函数加该参数并写入 `Run.role_id`）。

- [ ] **Step 4: 路由层接线**

`routes/runs.py`：
- import 改 `from app.api.deps import db_session, require_current_principal, require_role` + `from app.core.roles import SystemRole`；删 `from app.core.permissions import PermissionCode`。
- `_can_read_run` / `_can_manage_run` 改为：

```python
def _can_read_run(principal: Principal, owner: str) -> bool:
    return principal.role == SystemRole.ADMIN or owner == principal.username

def _can_manage_run(principal: Principal, owner: str) -> bool:
    return principal.role == SystemRole.ADMIN or owner == principal.username
```

- `create_and_distribute` 守卫改 `require_role(SystemRole.ADMIN, SystemRole.USER)`，并解析 pool：

```python
@router.post("/eval-tasks/create-and-distribute", response_model=CreateDistributeResponse,
             status_code=status.HTTP_201_CREATED)
def create_and_distribute(body: CreateDistributeRequest,
                          session: Session = Depends(db_session),
                          principal: Principal = Depends(require_role(SystemRole.ADMIN, SystemRole.USER))) -> CreateDistributeResponse:
    from app.model import repo_auth
    from app.core.roles import ROLE_TYPE_POOL
    name_to_id = {r.name: r.role_id for r in repo_auth.list_roles(session, type=ROLE_TYPE_POOL)}
    role_id = run_service.resolve_pool_role(
        role=principal.role, user_pools=principal.pools,
        requested=getattr(body, "pool_name", None), name_to_id=name_to_id,
    )
    return run_service.create_and_distribute(session, body, owner=principal.username, role_id=role_id)
```

`schema/runs.py` 的 `CreateDistributeRequest` 增加可选字段：

```python
    pool_name: str | None = None
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest tests/api/test_runs_pool.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/runs.py backend/app/service/run_service.py backend/app/schema/runs.py backend/tests/api/test_runs_pool.py
git commit -m "feat: bind task to pool and role-based task isolation"
```

---

### Task 13: worker_protocol / enroll 守卫改 `require_role`

**Files:**
- Modify: `backend/app/api/routes/worker_protocol.py`, `backend/app/api/routes/enroll.py`

- [ ] **Step 1: 改 worker_protocol 守卫**

`routes/worker_protocol.py`：import 改 `from app.api.deps import db_session, require_role` + `from app.core.roles import SystemRole`；删 permissions import。所有 6 处 `dependencies=[Depends(require_permission(PermissionCode.WORKER_PROTOCOL_USE/ASSETS_USE))]` 改为 `dependencies=[Depends(require_role(SystemRole.BOT, SystemRole.ADMIN))]`。

- [ ] **Step 2: 给 enroll 路由加 admin 守卫**

`routes/enroll.py`：

```python
from app.api.deps import require_role
from app.core.roles import SystemRole

router = APIRouter(dependencies=[Depends(require_role(SystemRole.ADMIN))])
```

（顶部补 `from fastapi import APIRouter, Depends, Query, Request`。）

- [ ] **Step 3: 验证导入**

Run: `cd backend && uv run python -c "from app.api import router"`
Expected: 无报错

- [ ] **Step 4: 改写既有鉴权矩阵测试**

`tests/api/test_rbac_existing_routes.py` 与 `tests/api/test_worker_protocol_api.py`：把基于权限码的 token 构造改为基于 role 的 Principal（用 `auth_service.issue_token(Principal(..., role=..., pools=[]))` 或直接在 `AEO_ALLOW_NO_AUTH` 下测 admin 放行 + 显式构造 user/bot token 测拒绝）。最小覆盖：
- admin token 可访问 `/api/users`、`/api/pools`、`/api/workers`。
- user token 访问 `/api/users` → 403、`/api/pools` → 403、创建任务 → 允许。
- bot token 访问 `/api/workers/register` → 允许、`/api/users` → 403。

- [ ] **Step 5: Run**

Run: `cd backend && uv run pytest tests/api/test_rbac_existing_routes.py tests/api/test_worker_protocol_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/worker_protocol.py backend/app/api/routes/enroll.py backend/tests/api/test_rbac_existing_routes.py backend/tests/api/test_worker_protocol_api.py
git commit -m "feat: role-based guards for worker protocol and enroll"
```

---

### Task 14: 调度器按 pool 过滤候选机器

**Files:**
- Modify: `backend/app/service/orchestration/scheduler.py`
- Test: `backend/tests/orchestration/test_scheduler.py`（增用例）

- [ ] **Step 1: Write failing test**

```python
# tests/orchestration/test_scheduler.py（新增用例，沿用文件已有的建数据 helper）
from app.model import repo_workers, repo_runs, repo_batches
from app.service.orchestration import scheduler


def _online_worker(session, wid, role_id=None):
    w = repo_workers.upsert_worker(session, worker_id=wid, display_name=wid, host="h",
                                   slots_total=1, capabilities={})
    w.status = "online"
    w.role_id = role_id
    session.flush()


def test_scheduler_only_assigns_within_pool(session):
    repo_workers.__dict__  # ensure import
    _online_worker(session, "w_pool_a", role_id="role-a")
    _online_worker(session, "w_pool_b", role_id="role-b")
    # a run bound to pool role-a -> its queued batch must land on w_pool_a
    run = repo_runs.create_run(session, template_id="t1", owner="alice",
                               display_name="r", role_id="role-a")
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice",
                                      executor_kind="harbor-docker", batch_root="/tmp")
    repo_batches.set_status(session, batch.batch_id, "queued")
    session.commit()

    scheduler.assign_once(session)
    session.refresh(batch)
    assert batch.assigned_worker_id == "w_pool_a"
```

> 注：上面 `repo_runs.create_run` / `repo_batches.create_batch` / `set_status` 的精确签名以现有 repo 为准；执行时先 `grep "def create_run\|def create_batch\|def set_status\|def assign" backend/app/model/repo_runs.py backend/app/model/repo_batches.py` 对齐参数名，必要时调整 helper。

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/orchestration/test_scheduler.py -v`
Expected: FAIL（当前 scheduler 不看 pool，可能把 batch 分到 w_pool_b）

- [ ] **Step 3: 改 `scheduler.py` 的 `assign_once`**

```python
from app.model import repo_batches, repo_runs, repo_workers


def assign_once(session: Session) -> int:
    queued = repo_batches.list_by_status(session, "queued")
    if not queued:
        return 0

    free: dict[str, int] = {}
    weight: dict[str, float] = {}
    worker_pool: dict[str, str | None] = {}
    for worker in repo_workers.list_workers(session, only_enabled=True):
        if worker.status != "online":
            continue
        available = worker.slots_total - worker.slots_used
        if available > 0:
            free[worker.worker_id] = available
            weight[worker.worker_id] = worker.allocation_weight
            worker_pool[worker.worker_id] = worker.role_id

    # 每个 batch 经由其 run 解析出目标 pool（None = 不限）
    run_pool: dict[str, str | None] = {}
    for batch in queued:
        if batch.run_id not in run_pool:
            run = repo_runs.get_run(session, batch.run_id)
            run_pool[batch.run_id] = run.role_id if run is not None else None

    assigned = 0
    for batch in queued:
        target = run_pool.get(batch.run_id)
        candidates = [
            wid for wid, slots in free.items()
            if slots > 0 and (target is None or worker_pool.get(wid) == target)
        ]
        if not candidates:
            continue
        if batch.preferred_worker_id in candidates:
            chosen = batch.preferred_worker_id
        else:
            chosen = max(candidates, key=lambda wid: (weight[wid], wid))
        repo_batches.assign(session, batch.batch_id, chosen)
        free[chosen] -= 1
        assigned += 1
    return assigned
```

> 注意把外层 `break`（无候选时）改为 `continue`：现在不同 batch 有不同 pool，某 batch 无候选不代表其它 batch 也无候选。确认 `repo_runs.get_run(session, run_id)` 存在（`grep "def get_run" backend/app/model/repo_runs.py`）；若名字不同按实际调整。

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/orchestration/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/service/orchestration/scheduler.py backend/tests/orchestration/test_scheduler.py
git commit -m "feat: scheduler filters candidates by pool"
```

---

### Task 15: auth 路由、main 启动、全量后端回归

**Files:**
- Modify: `backend/app/api/routes/auth.py`, `backend/app/main.py`

- [ ] **Step 1: 改 `routes/auth.py` 的 `_principal_read`**

```python
def _principal_read(principal: auth_service.Principal) -> PrincipalRead:
    return PrincipalRead(
        username=principal.username,
        source=principal.source,
        role=principal.role,
        pools=principal.pools,
    )
```

- [ ] **Step 2: 改 `main.py` 启动引导**

把 `repo_auth.bootstrap_rbac(session)` 改为 `repo_auth.bootstrap_roles(session)`。

- [ ] **Step 3: 全量后端测试**

Run: `cd backend && uv run pytest -q`
Expected: 全绿。逐个修复遗留对 `permissions`/`groups`/`PermissionCode` 的引用（`grep -rn "PermissionCode\|bootstrap_rbac\|require_permission\|permissions_for_user\|group_names_for_user" backend/app backend/tests` 应为空）。

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/auth.py backend/app/main.py
git commit -m "feat: principal read and bootstrap use roles"
```

- [ ] **Step 5: 删除残留 `core/permissions.py`**

确认无引用后：

```bash
git rm backend/app/core/permissions.py
cd backend && uv run pytest -q
git commit -am "chore: remove permission code module"
```

---

## Phase D — 前端

> 前端改动遵循各页面现有写法（TanStack Query + `app/lib/api.ts` 的 `getJSON/postJSON/patchJSON` + `app/components/ui.tsx` 组件）。每个 Task 末尾用 `cd frontend && pnpm build` 作为验证。

### Task 16: `me` 类型与导航按 role 过滤

**Files:**
- Modify: `frontend/app/root.tsx`，以及定义 `currentUser`/`Me` 类型处（`grep -rn "currentUser\|permission:" frontend/app`）。

- [ ] **Step 1: 更新 Me 类型**

把 `me` 查询返回类型由 `{ groups, permissions }` 改为 `{ username: string; source: string; role: "admin"|"user"|"bot"; pools: string[] }`。

- [ ] **Step 2: 导航项改按 role**

`root.tsx`：`baseNavItems` 里把 `permission` 字段换成 `roles: string[]`。例：

```ts
const baseNavItems = [
  { to: "/", label: "任务", roles: ["admin", "user"], icon: ListChecks },
  { to: "/create", label: "新建任务", roles: ["admin", "user"], icon: Plus },
  { to: "/workers", label: "机器管理", roles: ["admin"], icon: HardDrive },
  { to: "/users", label: "用户管理", roles: ["admin"], icon: UserRound },
];
```

过滤逻辑由 `permissionItems`/`baseNavItems.filter(... permission in me.permissions)` 改为 `item.roles.includes(me.role)`。删除“权限控制”分组与 `/groups` 入口（groups 页将删除）。

- [ ] **Step 3: 验证**

Run: `cd frontend && pnpm build`
Expected: 构建通过，无 TS 报错。

- [ ] **Step 4: Commit**

```bash
git add frontend/app/root.tsx frontend/app/lib/
git commit -m "feat: role-based navigation"
```

---

### Task 17: 用户页 role 单选 + pools 多选

**Files:**
- Modify: `frontend/app/routes/users.tsx`

- [ ] **Step 1: 拉取 pools 选项**

新增查询：`const pools = useQuery({ queryKey: ["pools"], queryFn: () => getJSON<{pools: Pool[]}>("/api/pools") })`（`Pool = { roleId: string; name: string; displayName: string }`）。

- [ ] **Step 2: 表单字段**

用户新建/编辑表单：
- 角色：单选（`admin` / `user` / `bot`），提交字段 `role`。
- 所属机器池：多选复选框，列出 `pools.data.pools`，提交字段 `pools`（name 数组）。
提交 payload：`{ username, displayName, password, role, pools }`（创建）；编辑 PATCH `{ displayName?, isActive?, role?, pools? }`。
列表列展示 `role` 与 `pools`（join 显示）。

- [ ] **Step 3: 验证**

Run: `cd frontend && pnpm build`
Expected: 通过。

- [ ] **Step 4: 手测（可选）**

启动后端（`AEO_ALLOW_NO_AUTH=1`）+ `pnpm dev`，新建用户选角色与池，确认提交成功且列表回显。

- [ ] **Step 5: Commit**

```bash
git add frontend/app/routes/users.tsx
git commit -m "feat: user form with role and pool selection"
```

---

### Task 18: 机器页资源池管理 + 指派池

**Files:**
- Modify: `frontend/app/routes/workers.tsx`

- [ ] **Step 1: 资源池区块**

页面顶部新增“资源池”卡片：
- 列表：`getJSON("/api/pools")` → 展示 name / displayName / workerCount / memberCount。
- 新建：表单 `{ name, displayName }` → `postJSON("/api/pools", ...)`。
- 重命名：`patchJSON("/api/pools/{roleId}", { displayName })`。
- 删除：`apiFetch("/api/pools/{roleId}", { method: "DELETE" })`，捕获 400/409 错误提示“池内仍有机器/成员”。

- [ ] **Step 2: 机器行指派池**

每台机器一个下拉（选项来自 pools + “未分配”），变更调用 `patchJSON("/api/workers/{workerId}", { roleId })`（roleId 可为 null）。机器列表展示当前所属池。

- [ ] **Step 3: 验证**

Run: `cd frontend && pnpm build`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add frontend/app/routes/workers.tsx
git commit -m "feat: pool management and worker pool assignment on workers page"
```

---

### Task 19: 新建任务页资源池下拉

**Files:**
- Modify: `frontend/app/routes/create.tsx`

- [ ] **Step 1: 池下拉逻辑**

- 读取当前用户（`me` 查询，已含 `role`/`pools`）。
- 若 `role === "user"`：
  - `pools.length === 0` → 禁用提交，提示“尚未分配资源池，请联系管理员”。
  - `pools.length === 1` → 不显示下拉，提交时带 `poolName = pools[0]`。
  - `pools.length > 1` → 显示下拉（必选），提交 `poolName`。
- 若 `role === "admin"`：显示可选下拉（选项来自 `/api/pools`，含“不限”），不选则不带 `poolName`。
- 提交 body 在原有 create payload 上加 `poolName`（仅在有值时）。

- [ ] **Step 2: 验证**

Run: `cd frontend && pnpm build`
Expected: 通过。

- [ ] **Step 3: Commit**

```bash
git add frontend/app/routes/create.tsx
git commit -m "feat: pool selection on task creation"
```

---

### Task 20: 删除 groups 页与路由

**Files:**
- Delete: `frontend/app/routes/groups.tsx`
- Modify: `frontend/app/main.tsx`

- [ ] **Step 1: 删页面与路由**

```bash
git rm frontend/app/routes/groups.tsx
```

`main.tsx`：删 `import GroupsPage ...` 与 `{ path: "groups", element: <GroupsPage /> }`。

- [ ] **Step 2: 验证**

Run: `cd frontend && pnpm build`
Expected: 通过，无对 GroupsPage 的悬空引用。

- [ ] **Step 3: Commit**

```bash
git add frontend/app/main.tsx
git commit -m "chore: remove group management page"
```

---

## Phase E — 文档与收尾

### Task 21: README / .env.example / 旧 spec 标注

**Files:**
- Modify: `README.md`, `.env.example`, `docs/superpowers/specs/2026-06-07-user-auth-rbac-design.md`

- [ ] **Step 1: README**

更新认证/权限段落：说明三系统角色 + 资源池模型；机器页指派 pool；普通用户看不到机器/用户管理页。删除任何关于 12 权限码 / 组权限编辑的描述。

- [ ] **Step 2: `.env.example`**

确认 `AEO_ADMIN_*` / `AEO_BOT_*` / `AEO_AUTH_SECRET` / `AEO_ACCESS_TOKEN_TTL_MINUTES` 说明仍准确；移除任何 `AEO_TOKEN` 残留说明。

- [ ] **Step 3: 旧 spec 顶部加状态标注**

在 `2026-06-07-user-auth-rbac-design.md` 顶部加一行：`> 状态：已被 2026-06-09-rbac-role-pool-simplification-design.md 取代。`

- [ ] **Step 4: 最终全量验证**

Run: `cd backend && uv run pytest -q && cd ../frontend && pnpm build`
Expected: 后端全绿，前端构建通过。

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example docs/superpowers/specs/2026-06-07-user-auth-rbac-design.md
git commit -m "docs: update auth docs for role + pool model"
```

---

## 收尾检查清单

- [ ] `grep -rn "PermissionCode\|require_permission\|bootstrap_rbac\|group_permissions\|permissions_for_user" backend/app backend/tests` 为空。
- [ ] `grep -rn "/groups\|permissions" frontend/app` 无残留权限码/组逻辑。
- [ ] 后端 `uv run pytest -q` 全绿；前端 `pnpm build` 通过。
- [ ] 手测：admin 登录可见全部页面与任务；user 登录看不到机器/用户管理页、只见自己任务、创建任务受 pool 约束；bot 账号可完成 worker 注册/心跳。
