# Full-Stack Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Agent Eval Orchestrator into a layered FastAPI + Pydantic + SQLAlchemy/Alembic backend (`backend/app/{core,model,schema,service,api}`) and a Vite + React SPA (`frontend/`), removing all SSH/provision code in favor of a copy-paste worker enroll script and HTTP-only file transfer.

**Architecture:** `controller + worker` stays. The controller is a single FastAPI process: API routes are thin, business logic lives in `service/`, persistence in `model/` (SQLAlchemy 2.0 over sqlite), background orchestration runs as synchronous daemon threads started/stopped by the FastAPI `lifespan`. The worker is a rewritten polling daemon that pulls assets and streams results over the same token-authed HTTP channel. The SPA is built by Vite and served as static files by FastAPI.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.0, Alembic, uvicorn, pytest + httpx, uv (backend); React 19, Vite 7, TypeScript, Tailwind v4, shadcn/ui, TanStack Query/Table, react-router (client), pnpm (frontend).

**Spec:** `docs/superpowers/specs/2026-06-06-full-stack-redesign-design.md`

---

## Conventions (read once before starting)

- **Backend package** is `app`, rooted at `backend/`. Run all backend commands from `backend/`: `cd backend && uv run …`. Tests import `from app.…`.
- **TDD**: every code task writes the failing test first, runs it red, implements, runs it green, commits. Run a single test with `uv run pytest <path>::<name> -v`.
- **Ported modules**: several modules move almost verbatim from `src/agent_eval_orchestrator/` to `backend/app/…`. Those tasks are "move + rewrite imports + characterization test", not rewrites. The old tree stays untouched until Phase 6 deletes it, so you can always diff against it.
- **JSON columns**: ORM models use SQLAlchemy `JSON` type; in Python they are `dict`/`list`, never JSON strings. No manual `json.dumps/loads` in repos/services.
- **Timestamps**: ISO-8601 UTC strings via `app.core.ids.now_iso()`.
- **IDs**: `app.core.ids.new_id("<prefix>")`.
- **Auth**: shared token from env `AEO_TOKEN`. Accepted via header `X-AEO-Token` or query `?token=`. Only `GET /api/health` is unauthenticated.
- **Commits**: do NOT add `Co-Authored-By` trailers (user preference). Use `feat:`/`test:`/`chore:`/`docs:` prefixes.

---

## File Structure

```
backend/
├── pyproject.toml                 # deps, pytest config, package = app
├── alembic.ini                    # alembic config (script_location, sqlalchemy.url from env)
├── alembic/
│   ├── env.py                     # target_metadata = app.model.base.Base.metadata; render_as_batch
│   └── versions/0001_init.py      # baseline migration (all 7 tables)
├── app/
│   ├── __init__.py
│   ├── main.py                    # create_app(): router, lifespan, static mount
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings (pydantic-settings), get_settings()
│   │   ├── defaults.py            # ported constants (no SSH paths)
│   │   ├── ids.py                 # ported now_iso/new_id/sanitize_name/safe_timestamp
│   │   ├── layout.py              # ported Layout (+ imported_jobs_dir)
│   │   └── worker_paths.py        # ported (no SSH/provision helpers)
│   ├── model/
│   │   ├── __init__.py
│   │   ├── base.py                # DeclarativeBase
│   │   ├── db.py                  # engine, SessionLocal, get_session(), get_db()
│   │   ├── tables.py              # 7 ORM models
│   │   ├── repo_templates.py
│   │   ├── repo_runs.py
│   │   ├── repo_batches.py
│   │   ├── repo_case_runs.py
│   │   ├── repo_workers.py
│   │   ├── repo_asset_sync_jobs.py
│   │   └── repo_rerun_jobs.py
│   ├── schema/
│   │   ├── __init__.py
│   │   ├── common.py              # ApiModel base (camelCase alias)
│   │   ├── health.py
│   │   ├── templates.py
│   │   ├── runs.py
│   │   ├── batches.py
│   │   ├── case_runs.py
│   │   ├── workers.py
│   │   ├── dashboard.py
│   │   ├── datasets.py
│   │   ├── worker_protocol.py     # register/claim/heartbeat/job-archive + asset contract
│   │   └── assets.py              # AssetManifest, AssetEntry
│   ├── service/
│   │   ├── __init__.py
│   │   ├── errors.py              # ServiceError + subclasses
│   │   ├── template_service.py
│   │   ├── run_service.py
│   │   ├── batch_service.py
│   │   ├── worker_service.py
│   │   ├── dashboard_service.py
│   │   ├── dataset_service.py
│   │   ├── asset_service.py       # build/serve asset manifests + tar
│   │   ├── enroll_service.py      # render enroll.sh, serve code bundle
│   │   ├── files_service.py
│   │   ├── status.py              # ported case/batch status helpers (from store.py)
│   │   ├── executors/             # ported: base.py, harbor.py
│   │   ├── normalizers/           # ported: harbor.py, harbor_job_merge.py, harbor_timestamps.py
│   │   └── orchestration/
│   │       ├── __init__.py
│   │       ├── manager.py         # OrchestrationManager: start/stop threads
│   │       ├── scheduler.py       # queued→assigned loop
│   │       ├── reaper.py          # heartbeat timeout loop
│   │       ├── result_collector.py# job-archive merge (ported from server.py helpers)
│   │       ├── rerun_coordinator.py # ported run_rerun_coordinator.py
│   │       ├── asset_syncer.py    # ported asset_syncer.py (local + http, no ssh)
│   │       └── viewer_manager.py  # ported harbor_viewer.py
│   └── api/
│       ├── __init__.py
│       ├── deps.py                # require_token, db session dep
│       ├── router.py              # aggregate
│       └── routes/
│           ├── __init__.py
│           ├── health.py
│           ├── templates.py
│           ├── runs.py
│           ├── batches.py
│           ├── case_runs.py
│           ├── workers.py
│           ├── dashboard.py
│           ├── datasets.py
│           ├── files.py
│           ├── harbor_viewer.py
│           ├── enroll.py
│           └── worker_protocol.py # register/claim/heartbeat/job-archive/assets
│   └── tests/  → backend/tests/   # see below
├── tests/
│   ├── conftest.py                # app + temp-db + client fixtures
│   ├── core/ model/ schema/ service/ api/ orchestration/ worker/
scripts/
├── enroll.sh.tmpl                 # template rendered by enroll_service
├── start-controller.sh
├── stop-controller.sh
└── start-worker.sh
backend/app/worker/
├── __init__.py
└── daemon.py                      # rewritten worker
frontend/                          # Vite + React SPA (Phase 7)
```

---

# Phase 0 — Backend scaffolding & tooling

### Task 0.1: Create backend project skeleton

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py` (empty)
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/.gitignore`

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[project]
name = "agent-eval-orchestrator-backend"
version = "0.1.0"
description = "Agent Eval Orchestrator controller (FastAPI)."
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
exclude = ["tests*", "alembic*"]
```

- [ ] **Step 2: Create empty package markers**

Create `backend/app/__init__.py` and `backend/tests/__init__.py` as empty files. Create `backend/.gitignore`:

```gitignore
__pycache__/
*.pyc
.venv/
*.sqlite3
*.db
```

- [ ] **Step 3: Initialize the uv environment**

Run: `cd backend && uv venv && uv pip install -e ".[dev]"`
Expected: resolves and installs fastapi, sqlalchemy, alembic, pytest, httpx with no errors.

- [ ] **Step 4: Sanity check pytest discovers zero tests**

Run: `cd backend && uv run pytest -q`
Expected: `no tests ran` (exit code 5) — confirms config is valid.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app/__init__.py backend/tests/__init__.py backend/.gitignore
git commit -m "chore: scaffold backend FastAPI project"
```

---

# Phase 1 — Core, models, repos, schema, migrations

### Task 1.1: Port core constants and id helpers

**Files:**
- Create: `backend/app/core/__init__.py` (empty)
- Create: `backend/app/core/ids.py`
- Create: `backend/app/core/defaults.py`
- Test: `backend/tests/core/test_ids.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/core/test_ids.py` (also create empty `backend/tests/core/__init__.py`)

```python
from app.core.ids import new_id, now_iso, sanitize_name


def test_new_id_has_prefix():
    value = new_id("run")
    assert value.startswith("run-")
    assert len(value) == len("run-") + 12


def test_now_iso_is_utc():
    assert now_iso().endswith("+00:00")


def test_sanitize_name_strips_unsafe():
    assert sanitize_name("a b/c!") == "a-b-c"
    assert sanitize_name("   ") == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/core/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core'`.

- [ ] **Step 3: Implement `app/core/ids.py`**

Copy the body of `src/agent_eval_orchestrator/core/ids.py` verbatim (functions `now_iso`, `today_iso`, `safe_timestamp`, `sanitize_name`, `new_id`). It has no internal imports to rewrite.

- [ ] **Step 4: Implement `app/core/defaults.py`**

Copy `src/agent_eval_orchestrator/core/defaults.py` verbatim **except** change the two hard-coded `/root/projects/...` paths to be overridable later by settings — for now keep the constants but remove SSH-only ones. Keep all constants shown in the source. Delete nothing except: there are no SSH constants in this file, so copy as-is.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/core/test_ids.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/ backend/tests/core/
git commit -m "feat: port core ids and defaults to backend"
```

### Task 1.2: Settings (pydantic-settings)

**Files:**
- Create: `backend/app/core/config.py`
- Test: `backend/tests/core/test_config.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/core/test_config.py`

```python
from pathlib import Path

from app.core.config import Settings


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    s = Settings()
    assert s.shared_root == tmp_path
    assert s.database_url == f"sqlite:///{tmp_path}/controller/aeo.db"
    assert s.token is None


def test_explicit_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/x.db")
    assert Settings().database_url == "sqlite:////tmp/x.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/core/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.config'`.

- [ ] **Step 3: Implement `app/core/config.py`**

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.defaults import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SHARED_ROOT, DEFAULT_HARBOR_REPO


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEO_", env_file=".env", extra="ignore")

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    shared_root: Path = Field(default=DEFAULT_SHARED_ROOT)
    harbor_repo: Path = Field(default=DEFAULT_HARBOR_REPO)
    token: str | None = Field(default=None, alias="AEO_TOKEN")
    allow_no_auth: bool = Field(default=False, alias="AEO_ALLOW_NO_AUTH")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    @model_validator(mode="after")
    def _derive_database_url(self) -> "Settings":
        if not self.database_url:
            db = self.shared_root / "controller" / "aeo.db"
            object.__setattr__(self, "database_url", f"sqlite:///{db}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Note: `token` and `database_url` use explicit non-prefixed aliases (`AEO_TOKEN` matches the prefix anyway; `DATABASE_URL` is intentionally un-prefixed). Pydantic-settings honors `alias` over the env_prefix.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/core/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/core/test_config.py
git commit -m "feat: add Settings via pydantic-settings"
```

### Task 1.3: Port layout + worker_paths

**Files:**
- Create: `backend/app/core/layout.py`
- Create: `backend/app/core/worker_paths.py`
- Test: `backend/tests/core/test_layout.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/core/test_layout.py`

```python
from app.core.layout import Layout, default_layout


def test_layout_dirs(tmp_path):
    layout = Layout(root=tmp_path)
    assert layout.controller_dir == tmp_path / "controller"
    assert layout.imported_jobs_dir == tmp_path / "controller" / "imported-jobs"
    layout.ensure_dirs()
    assert layout.controller_dir.is_dir()


def test_default_layout(tmp_path):
    assert default_layout(tmp_path).root == tmp_path.resolve()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/core/test_layout.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `app/core/layout.py`**

Copy `src/agent_eval_orchestrator/storage/layout.py`, rewriting imports from `agent_eval_orchestrator.core.*` to `app.core.*`, and **add** an `imported_jobs_dir` property and have `ensure_dirs` create it:

```python
    @property
    def imported_jobs_dir(self) -> Path:
        return self.controller_dir / "imported-jobs"

    def ensure_dirs(self) -> None:
        for path in (self.controller_dir, self.archives_dir, self.workers_dir, self.imported_jobs_dir):
            path.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Implement `app/core/worker_paths.py`**

Copy `src/agent_eval_orchestrator/core/worker_paths.py`, rewriting imports to `app.core.*`. Remove any function whose only purpose is SSH/provision (inspect: keep `*_from_shared_root`, `build_harbor_bind_mounts`, `build_sync_bind_mounts`, `default_bitfun_config_dir`, `default_uv_binary`). If a helper references provisioner constants, drop that helper.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/core/test_layout.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/layout.py backend/app/core/worker_paths.py backend/tests/core/test_layout.py
git commit -m "feat: port layout and worker_paths"
```

### Task 1.4: SQLAlchemy Base + engine/session

**Files:**
- Create: `backend/app/model/__init__.py` (empty)
- Create: `backend/app/model/base.py`
- Create: `backend/app/model/db.py`
- Test: `backend/tests/model/test_db.py` (+ empty `backend/tests/model/__init__.py`)

- [ ] **Step 1: Write the failing test** — `backend/tests/model/test_db.py`

```python
from sqlalchemy import text

from app.model.db import make_engine, make_session_factory


def test_engine_uses_wal(tmp_path):
    url = f"sqlite:///{tmp_path}/x.db"
    engine = make_engine(url)
    Session = make_session_factory(engine)
    with Session() as s:
        mode = s.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/model/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.db'`.

- [ ] **Step 3: Implement `app/model/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 4: Implement `app/model/db.py`**

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _factory() -> sessionmaker[Session]:
    global _engine, _Session
    if _Session is None:
        url = get_settings().database_url
        assert url is not None
        # ensure parent dir exists for sqlite file
        if url.startswith("sqlite:///"):
            from pathlib import Path

            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = make_engine(url)
        _Session = make_session_factory(_engine)
    return _Session


@contextmanager
def get_session() -> Iterator[Session]:
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session (no auto-commit; routes commit explicitly via services)."""
    session = _factory()()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/model/test_db.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/model/__init__.py backend/app/model/base.py backend/app/model/db.py backend/tests/model/
git commit -m "feat: add SQLAlchemy base, engine, and session factory"
```

### Task 1.5: ORM tables (all 7)

**Files:**
- Create: `backend/app/model/tables.py`
- Test: `backend/tests/model/test_tables.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/model/test_tables.py`

```python
from sqlalchemy import inspect

from app.model.base import Base
from app.model.db import make_engine
import app.model.tables  # noqa: F401  (registers models)

EXPECTED = {
    "task_templates", "runs", "batches", "case_runs",
    "workers", "asset_sync_jobs", "run_rerun_jobs",
}


def test_metadata_has_all_tables():
    assert EXPECTED.issubset(set(Base.metadata.tables))


def test_create_all(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/x.db")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert EXPECTED.issubset(names)


def test_worker_has_no_ssh_columns():
    cols = {c.name for c in Base.metadata.tables["workers"].columns}
    assert "ssh_host_alias" not in cols
    assert "connection_mode" not in cols
    assert {"worker_id", "slots_total", "slots_used", "allocation_weight"}.issubset(cols)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/model/test_tables.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.tables'`.

- [ ] **Step 3: Implement `app/model/tables.py`**

```python
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import now_iso
from app.model.base import Base


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    template_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    dataset_ref: Mapped[str] = mapped_column(String, nullable=False)
    executor_kind: Mapped[str] = mapped_column(String, nullable=False)
    executor_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_profile_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Run(Base):
    __tablename__ = "runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    bound_worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="")
    sync_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rerun_status: Mapped[str] = mapped_column(String, nullable=False, default="idle")
    rerun_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Batch(Base):
    __tablename__ = "batches"
    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assigned_worker_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    executor_kind: Mapped[str] = mapped_column(String, nullable=False)
    executor_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    selected_case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    batch_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifact_index: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    batch_root: Mapped[str] = mapped_column(String, nullable=False)
    parent_batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    batch_kind: Mapped[str] = mapped_column(String, nullable=False, default="primary")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseRun(Base):
    __tablename__ = "case_runs"
    case_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifact_index: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class Worker(Base):
    __tablename__ = "workers"
    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    slots_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    slots_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allocation_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class AssetSyncJob(Base):
    __tablename__ = "asset_sync_jobs"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class RunRerunJob(Base):
    __tablename__ = "run_rerun_jobs"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    sync_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    worker_shards: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rerun_batches: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    selected_error_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/model/test_tables.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/model/tables.py backend/tests/model/test_tables.py
git commit -m "feat: define 7 ORM tables (no SSH columns)"
```

### Task 1.6: Alembic env + baseline migration

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/0001_init.py` (autogenerated then committed)
- Test: `backend/tests/model/test_migrations.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/model/test_migrations.py`

```python
import subprocess
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_creates_tables(tmp_path):
    db = tmp_path / "m.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": __import__("os").environ["PATH"]}
    out = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd="."  , env=env, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    names = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"task_templates", "runs", "batches", "case_runs", "workers"}.issubset(names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/model/test_migrations.py -v`
Expected: FAIL (alembic not configured / command errors).

- [ ] **Step 3: Write `backend/alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: Write `backend/alembic/env.py`**

```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.model.base import Base
import app.model.tables  # noqa: F401  (register models)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Prefer an explicit env override; otherwise fall back to the app's derived
    # default (sqlite under shared_root) so bare `alembic upgrade head` works.
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    from app.core.config import get_settings

    derived = get_settings().database_url
    if not derived:
        raise RuntimeError("DATABASE_URL is required for alembic")
    return derived


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Add the standard `backend/alembic/script.py.mako`**

Copy the standard Alembic mako template (the default emitted by `alembic init`). It must define `revision`, `down_revision`, `upgrade()`, `downgrade()` placeholders. Use the verbatim template from `src`'s sibling reference `/home/djn/code/d4a-platform/backend/alembic/script.py.mako` if present; otherwise the alembic default.

- [ ] **Step 6: Generate the baseline migration**

Run:
```bash
cd backend && DATABASE_URL="sqlite:///$(pwd)/.tmp-gen.db" uv run alembic revision --autogenerate -m "init core tables"
```
Then rename the generated file in `alembic/versions/` to `0001_init.py`, set `revision = "0001_init"` and `down_revision = None` inside it. Delete `.tmp-gen.db`. Inspect the file: it must `op.create_table(...)` for all 7 tables with the columns from Task 1.5.

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/model/test_migrations.py -v`
Expected: PASS (1 passed).

- [ ] **Step 8: Commit**

```bash
git add backend/alembic.ini backend/alembic/ backend/tests/model/test_migrations.py
git commit -m "feat: add alembic env and baseline migration"
```

### Task 1.7: Shared test fixtures (temp DB)

**Files:**
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Write `backend/tests/conftest.py`**

```python
import pytest
from sqlalchemy import event

from app.model.base import Base
import app.model.tables  # noqa: F401
from app.model.db import make_engine, make_session_factory


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = make_session_factory(engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && uv run pytest tests/model -q`
Expected: existing model tests still PASS (fixture import does not break collection).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test: add session fixture with temp sqlite db"
```

### Task 1.8: Workers repository (canonical repo pattern)

**Files:**
- Create: `backend/app/model/repo_workers.py`
- Test: `backend/tests/model/test_repo_workers.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/model/test_repo_workers.py`

```python
from app.model import repo_workers as repo


def test_upsert_and_get(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h",
                       slots_total=2, capabilities={"cpu": 8})
    session.commit()
    w = repo.get_worker(session, "w1")
    assert w is not None and w.slots_total == 2 and w.capabilities["cpu"] == 8


def test_list_enabled(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    repo.upsert_worker(session, worker_id="w2", display_name="W2", host="h", slots_total=1, capabilities={})
    repo.set_enabled(session, "w2", False)
    session.commit()
    ids = [w.worker_id for w in repo.list_workers(session, only_enabled=True)]
    assert ids == ["w1"]


def test_touch_heartbeat_updates_slots(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=4, capabilities={})
    session.commit()
    repo.update_runtime(session, "w1", slots_used=3, status="online", last_heartbeat_at="2026-01-01T00:00:00+00:00")
    session.commit()
    w = repo.get_worker(session, "w1")
    assert w.slots_used == 3 and w.status == "online"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/model/test_repo_workers.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `app/model/repo_workers.py`**

```python
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.model.tables import Worker


def get_worker(session: Session, worker_id: str) -> Worker | None:
    return session.get(Worker, worker_id)


def list_workers(session: Session, *, only_enabled: bool = False) -> list[Worker]:
    stmt = select(Worker).order_by(Worker.created_at)
    if only_enabled:
        stmt = stmt.where(Worker.enabled == 1)
    return list(session.scalars(stmt))


def upsert_worker(session: Session, *, worker_id: str, display_name: str, host: str,
                  slots_total: int, capabilities: dict[str, Any]) -> Worker:
    worker = session.get(Worker, worker_id)
    now = now_iso()
    if worker is None:
        worker = Worker(worker_id=worker_id, display_name=display_name, host=host,
                        slots_total=slots_total, slots_used=0, capabilities=capabilities,
                        status="online", enabled=1, note="", tags=[], allocation_weight=1.0,
                        last_heartbeat_at=now, created_at=now, updated_at=now)
        session.add(worker)
    else:
        worker.display_name = display_name
        worker.host = host
        worker.slots_total = slots_total
        worker.capabilities = capabilities
        worker.status = "online"
        worker.last_heartbeat_at = now
        worker.updated_at = now
    return worker


def update_runtime(session: Session, worker_id: str, *, slots_used: int | None = None,
                   status: str | None = None, last_heartbeat_at: str | None = None) -> None:
    worker = session.get(Worker, worker_id)
    if worker is None:
        return
    if slots_used is not None:
        worker.slots_used = slots_used
    if status is not None:
        worker.status = status
    if last_heartbeat_at is not None:
        worker.last_heartbeat_at = last_heartbeat_at
    worker.updated_at = now_iso()


def set_enabled(session: Session, worker_id: str, enabled: bool) -> None:
    worker = session.get(Worker, worker_id)
    if worker is not None:
        worker.enabled = 1 if enabled else 0
        worker.updated_at = now_iso()


def delete_worker(session: Session, worker_id: str) -> None:
    worker = session.get(Worker, worker_id)
    if worker is not None:
        session.delete(worker)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/model/test_repo_workers.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/model/repo_workers.py backend/tests/model/test_repo_workers.py
git commit -m "feat: add workers repository"
```

### Task 1.9: Remaining repositories (templates, runs, batches, case_runs, asset_sync_jobs, rerun_jobs)

Follow the **exact pattern of Task 1.8** for each repo below. Each gets its own file and its own `backend/tests/model/test_repo_<name>.py`. For each, write the failing test first (create + get + list + one mutation), run red, implement, run green, commit separately.

- [ ] **Step 1: `repo_templates.py`** — functions:
  - `create_template(session, *, owner, name, dataset_ref, executor_kind, executor_config, model_profile_ref=None, note="") -> TaskTemplate` (generates `template_id=new_id("tpl")`).
  - `get_template(session, template_id) -> TaskTemplate | None`
  - `list_templates(session, *, owner=None) -> list[TaskTemplate]` (order by created_at desc).
  Test asserts created id starts with `tpl-`, get returns it, list filters by owner.

- [ ] **Step 2: `repo_runs.py`** — functions:
  - `create_run(session, *, template_id, owner, display_name, parent_run_id=None) -> Run` (`run_id=new_id("run")`, sync_status="", rerun_status="idle", sync_manifest={}).
  - `get_run(session, run_id) -> Run | None`
  - `list_runs(session, *, owner=None) -> list[Run]`
  - `set_latest_batch(session, run_id, batch_id)`, `set_sync(session, run_id, *, status, job_id=None, manifest=None)`, `set_rerun(session, run_id, *, status, job_id=None)`.
  Test asserts create/get, set_latest_batch persists, set_sync updates status+job_id.

- [ ] **Step 3: `repo_batches.py`** — functions:
  - `create_batch(session, *, run_id, owner, executor_kind, selected_case_ids, batch_options, batch_root, preferred_worker_id=None, parent_batch_id=None, batch_kind="primary", executor_metadata=None) -> Batch` (`batch_id=new_id("batch")`, status="queued", summary={}, artifact_index={}).
  - `get_batch(session, batch_id) -> Batch | None`
  - `list_batches_for_run(session, run_id) -> list[Batch]`
  - `list_by_status(session, status) -> list[Batch]`
  - `assign(session, batch_id, worker_id)` (sets assigned_worker_id, status="assigned", current_step=None, updated started_at via now), `set_status(session, batch_id, status, *, current_step=None, error_text=None, started_at=None, finished_at=None)`, `set_summary(session, batch_id, summary, artifact_index)`.
  Test asserts create defaults status=queued, list_by_status("queued") returns it, assign sets worker+status.

- [ ] **Step 4: `repo_case_runs.py`** — functions:
  - `replace_for_batch(session, batch_id, cases: list[dict]) -> None` (delete existing rows for batch_id, insert each with `case_run_id=new_id("case")`, fields: case_id, status, score, metrics, artifact_index, error_text).
  - `list_for_batch(session, batch_id) -> list[CaseRun]`
  - `list_for_run(session, run_id) -> list[CaseRun]` (join via batches.run_id).
  Test asserts replace inserts N rows, calling again replaces (not appends), list_for_batch returns them.

- [ ] **Step 5: `repo_asset_sync_jobs.py`** — functions:
  - `create_job(session, *, run_id, steps) -> AssetSyncJob` (`job_id=new_id("sync")`, status="pending").
  - `get_job(session, job_id)`, `update_job(session, job_id, *, status=None, current_step=None, steps=None, log_append=None, error_text=None, finished_at=None)`.
  Test asserts create status=pending, update sets status + appends log_text.

- [ ] **Step 6: `repo_rerun_jobs.py`** — functions:
  - `create_job(session, *, run_id, case_ids, worker_shards, selected_error_types=None) -> RunRerunJob` (`job_id=new_id("rerun")`, status="pending", rerun_batches=[]).
  - `get_job(session, job_id)`, `update_job(session, job_id, *, status=None, sync_job_id=None, rerun_batches=None, error_text=None, finished_at=None)`.
  Test asserts create + update.

- [ ] **Step 7: Commit each repo separately** with `feat: add <name> repository`.

---

# Phase 2 — Schema layer (Pydantic)

### Task 2.1: ApiModel base + health schema + health route + app skeleton

**Files:**
- Create: `backend/app/schema/__init__.py` (empty), `backend/app/schema/common.py`, `backend/app/schema/health.py`
- Create: `backend/app/api/__init__.py` (empty), `backend/app/api/routes/__init__.py` (empty)
- Create: `backend/app/api/routes/health.py`, `backend/app/api/router.py`, `backend/app/api/deps.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/api/test_health.py` (+ empty `backend/tests/api/__init__.py`)

- [ ] **Step 1: Write the failing test** — `backend/tests/api/test_health.py`

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Implement `app/schema/common.py`**

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
```

- [ ] **Step 4: Implement `app/schema/health.py`**

```python
from app.schema.common import ApiModel


class HealthResponse(ApiModel):
    status: str
```

- [ ] **Step 5: Implement `app/api/deps.py`** (auth dependency, used by every router except health)

```python
from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.model.db import get_db


def db_session() -> Session:
    yield from get_db()


def require_token(request: Request, token: str | None = Query(default=None)) -> None:
    settings = get_settings()
    expected = settings.token
    if not expected:
        # Default-deny: refuse to serve protected routes unless a token is configured.
        # Only an explicit dev opt-in (AEO_ALLOW_NO_AUTH=1) leaves the API open.
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

- [ ] **Step 6: Implement `app/api/routes/health.py`**

```python
from fastapi import APIRouter

from app.schema.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
```

- [ ] **Step 7: Implement `app/api/router.py`** (aggregator; health is open, everything else added later requires token)

```python
from fastapi import APIRouter, Depends

from app.api.deps import require_token
from app.api.routes import health

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])

# Authenticated sub-routers are registered in app.main with a shared token dependency.
authed_router = APIRouter(dependencies=[Depends(require_token)])
```

- [ ] **Step 8: Implement `app/main.py`**

```python
from __future__ import annotations

from fastapi import FastAPI

from app.api.router import api_router, authed_router


def create_app() -> FastAPI:
    app = FastAPI(title="agent-eval-orchestrator")
    app.include_router(api_router)
    app.include_router(authed_router, prefix="/api")
    return app


app = create_app()
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_health.py -v`
Expected: PASS (1 passed).

- [ ] **Step 10: Commit**

```bash
git add backend/app/schema/ backend/app/api/ backend/app/main.py backend/tests/api/
git commit -m "feat: FastAPI app skeleton with health route and auth dep"
```

### Task 2.2: Entity schemas

**Files:** Create `backend/app/schema/{templates,runs,batches,case_runs,workers,dashboard,datasets}.py`. No tests of their own (exercised via route tests in Phase 4); validate by importing.

- [ ] **Step 1: Write `app/schema/workers.py`** (canonical — all others mirror this shape: a `Create`/request model + a `Read` response model subclassing `ApiModel`, fields matching the ORM columns that the API exposes)

```python
from typing import Any

from app.schema.common import ApiModel


class WorkerRead(ApiModel):
    worker_id: str
    display_name: str
    host: str
    slots_total: int
    slots_used: int
    capabilities: dict[str, Any]
    status: str
    enabled: bool
    note: str
    tags: list[str]
    allocation_weight: float
    last_heartbeat_at: str | None = None


class WorkerSettingsUpdate(ApiModel):
    enabled: bool | None = None
    note: str | None = None
    tags: list[str] | None = None
    allocation_weight: float | None = None
```

- [ ] **Step 2: Write `app/schema/templates.py`**

```python
from typing import Any
from app.schema.common import ApiModel


class TemplateCreate(ApiModel):
    owner: str = "demo"
    name: str
    dataset_ref: str
    executor_kind: str = "harbor"
    executor_config: dict[str, Any] = {}
    model_profile_ref: str | None = None
    note: str = ""


class TemplateRead(ApiModel):
    template_id: str
    owner: str
    name: str
    dataset_ref: str
    executor_kind: str
    executor_config: dict[str, Any]
    model_profile_ref: str | None = None
    note: str
    created_at: str
    updated_at: str
```

- [ ] **Step 3: Write `app/schema/runs.py`, `app/schema/batches.py`, `app/schema/case_runs.py`** — `Read` models mirroring the ORM columns from Task 1.5 (use the same field names in snake_case; `ApiModel` will serialize to camelCase). For `runs.py` add `RunCreate(ApiModel)` with `template_id, owner="demo", display_name`. For `batches.py` add `CaseRead`-style nesting if needed by including `BatchRead`. For `case_runs.py` add `CaseRunRead`.

- [ ] **Step 4: Write `app/schema/dashboard.py`**

```python
from typing import Any
from app.schema.common import ApiModel


class DashboardTask(ApiModel):
    run_id: str
    display_name: str
    owner: str
    status: str
    template_id: str
    latest_batch_id: str | None = None
    counts: dict[str, int] = {}
    updated_at: str


class DashboardTasksResponse(ApiModel):
    tasks: list[DashboardTask]
```

- [ ] **Step 5: Write `app/schema/datasets.py`**

```python
from app.schema.common import ApiModel


class DatasetInfo(ApiModel):
    dataset_ref: str
    available: bool
    path: str


class DatasetsResponse(ApiModel):
    datasets: list[DatasetInfo]
```

- [ ] **Step 6: Verify all schemas import**

Run: `cd backend && uv run python -c "import app.schema.templates, app.schema.runs, app.schema.batches, app.schema.case_runs, app.schema.workers, app.schema.dashboard, app.schema.datasets; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schema/
git commit -m "feat: add entity schemas"
```

### Task 2.3: Worker-protocol + asset schemas

**Files:** Create `backend/app/schema/worker_protocol.py`, `backend/app/schema/assets.py`. Test: `backend/tests/schema/test_worker_protocol.py` (+ empty `__init__.py`).

- [ ] **Step 1: Write the failing test** — `backend/tests/schema/test_worker_protocol.py`

```python
from app.schema.worker_protocol import ClaimResponse, RegisterRequest
from app.schema.assets import AssetEntry, AssetManifest


def test_register_request_camel():
    req = RegisterRequest.model_validate(
        {"workerId": "w1", "displayName": "W1", "host": "h", "slotsTotal": 2, "capabilities": {}}
    )
    assert req.worker_id == "w1" and req.slots_total == 2


def test_claim_response_carries_asset_contract():
    manifest = AssetManifest(
        asset_manifest_id="am-1",
        target_root_rel="sync/run-1",
        entries=[AssetEntry(path="cases/c1", size=10, sha256="abc", kind="case")],
    )
    resp = ClaimResponse(
        batch_id="batch-1", dataset_ref="d/x", executor_config={},
        asset_manifest_id="am-1", asset_url="/api/workers/assets/am-1", asset_manifest=manifest,
    )
    dumped = resp.model_dump(by_alias=True)
    assert dumped["assetManifestId"] == "am-1"
    assert dumped["assetManifest"]["entries"][0]["sha256"] == "abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/schema/test_worker_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `app/schema/assets.py`**

```python
from typing import Literal
from app.schema.common import ApiModel


class AssetEntry(ApiModel):
    path: str
    size: int
    sha256: str
    kind: Literal["case", "bitfun", "cli"]


class AssetManifest(ApiModel):
    asset_manifest_id: str
    target_root_rel: str
    entries: list[AssetEntry]
```

- [ ] **Step 4: Implement `app/schema/worker_protocol.py`**

```python
from typing import Any
from app.schema.common import ApiModel
from app.schema.assets import AssetManifest


class RegisterRequest(ApiModel):
    worker_id: str
    display_name: str
    host: str
    slots_total: int
    capabilities: dict[str, Any] = {}


class RegisterResponse(ApiModel):
    ok: bool = True
    worker_id: str


class ClaimRequest(ApiModel):
    worker_id: str


class ClaimResponse(ApiModel):
    batch_id: str | None = None
    dataset_ref: str | None = None
    executor_config: dict[str, Any] = {}
    asset_manifest_id: str | None = None
    asset_url: str | None = None
    asset_manifest: AssetManifest | None = None


class HeartbeatRequest(ApiModel):
    worker_id: str
    batch_id: str | None = None
    status: str | None = None          # running | succeeded | failed | sync_failed
    slots_used: int | None = None
    summary: dict[str, Any] | None = None
    cases: list[dict[str, Any]] | None = None
    error_text: str | None = None
    finished: bool = False


class HeartbeatResponse(ApiModel):
    ok: bool = True


class JobArchiveResponse(ApiModel):
    ok: bool = True
    batch_id: str
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/schema/test_worker_protocol.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schema/worker_protocol.py backend/app/schema/assets.py backend/tests/schema/
git commit -m "feat: add worker-protocol and asset schemas"
```

---

# Phase 3 — Port executors, normalizers, status helpers

### Task 3.1: Port executors (base + harbor)

**Files:**
- Create: `backend/app/service/__init__.py` (empty), `backend/app/service/executors/__init__.py` (empty)
- Create: `backend/app/service/executors/base.py`, `backend/app/service/executors/harbor.py`
- Test: `backend/tests/service/test_harbor_executor.py` (+ empty `__init__.py`)

- [ ] **Step 1: Write the failing characterization test** — port the assertions from `tests/executors/test_harbor_executor.py` (read that file), changing imports to `from app.service.executors.harbor import HarborExecutor`. Keep the same input/output assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/service/test_harbor_executor.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Port `base.py`** — copy `src/agent_eval_orchestrator/executors/base.py` verbatim (no internal imports to rewrite).

- [ ] **Step 4: Port `harbor.py`** — copy `src/agent_eval_orchestrator/executors/harbor.py`, rewriting imports: `agent_eval_orchestrator.core.*` → `app.core.*`, `agent_eval_orchestrator.executors.base` → `app.service.executors.base`. Do not change logic.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/service/test_harbor_executor.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/service/__init__.py backend/app/service/executors/ backend/tests/service/test_harbor_executor.py
git commit -m "feat: port harbor executor to service layer"
```

### Task 3.2: Port normalizers

**Files:**
- Create: `backend/app/service/normalizers/__init__.py` (empty)
- Create: `backend/app/service/normalizers/harbor.py`, `harbor_job_merge.py`, `harbor_timestamps.py`
- Create: `backend/app/service/status.py` (case/batch status helpers extracted from `store.py`)
- Test: `backend/tests/service/test_normalizers.py`, `backend/tests/service/test_status.py`

- [ ] **Step 1: Write characterization tests** — port relevant assertions from `tests/storage/test_case_status_helpers.py` into `test_status.py` (importing the helper functions you will extract), and a small `test_normalizers.py` that calls `normalize_harbor_job` on a fixture job dir copied from an existing test fixture under `tests/`.

- [ ] **Step 2: Run to verify red**

Run: `cd backend && uv run pytest tests/service/test_normalizers.py tests/service/test_status.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Port the three normalizer files** — copy from `src/agent_eval_orchestrator/normalizers/*.py`, rewriting imports to `app.*`. The `harbor.py` normalizer imports `harbor_exceptions` (case id / exception-type helpers) — move those two functions into `app/service/status.py` and import from there.

- [ ] **Step 4: Extract `app/service/status.py`** — copy from `store.py` these pure staticmethods/functions (they take dicts, no DB): `_case_is_errored`, `_case_is_failed`, `_overall_status_from_batch_counts`, `case_error_type`, plus `exception_type_from_text` and `harbor_trial_case_id` from `controller/harbor_exceptions.py`. Make them module-level functions with the same names minus leading underscore where appropriate; update the ported normalizer import accordingly.

- [ ] **Step 5: Run to verify green**

Run: `cd backend && uv run pytest tests/service/test_normalizers.py tests/service/test_status.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/service/normalizers/ backend/app/service/status.py backend/tests/service/test_normalizers.py backend/tests/service/test_status.py
git commit -m "feat: port harbor normalizers and status helpers"
```

---

# Phase 4 — Services + API routes (read paths first)

### Task 4.1: Service errors + template service + templates routes (canonical CRUD slice)

**Files:**
- Create: `backend/app/service/errors.py`, `backend/app/service/template_service.py`
- Create: `backend/app/api/routes/templates.py`
- Modify: `backend/app/api/router.py` (register templates under `authed_router`)
- Test: `backend/tests/api/test_templates_api.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/api/test_templates_api.py`

```python
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import db_session


@pytest.fixture
def client(session, monkeypatch):
    # Default-deny auth: tests run in explicit dev-open mode unless they set AEO_TOKEN.
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def test_create_and_list_template(client):
    payload = {"name": "t1", "datasetRef": "terminal-bench/terminal-bench-2"}
    resp = client.post("/api/task-templates", json=payload)
    assert resp.status_code == 201, resp.text
    tid = resp.json()["templateId"]
    assert tid.startswith("tpl-")
    listed = client.get("/api/task-templates").json()["templates"]
    assert any(t["templateId"] == tid for t in listed)
```

- [ ] **Step 2: Run to verify red**

Run: `cd backend && uv run pytest tests/api/test_templates_api.py -v`
Expected: FAIL (404 — route not registered).

- [ ] **Step 3: Implement `app/service/errors.py`**

```python
class ServiceError(Exception):
    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409
```

- [ ] **Step 4: Implement `app/service/template_service.py`**

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_templates
from app.model.tables import TaskTemplate
from app.schema.templates import TemplateCreate


def create_template(session: Session, data: TemplateCreate) -> TaskTemplate:
    tpl = repo_templates.create_template(
        session, owner=data.owner, name=data.name, dataset_ref=data.dataset_ref,
        executor_kind=data.executor_kind, executor_config=data.executor_config,
        model_profile_ref=data.model_profile_ref, note=data.note,
    )
    session.commit()
    return tpl


def list_templates(session: Session, owner: str | None = None) -> list[TaskTemplate]:
    return repo_templates.list_templates(session, owner=owner)
```

- [ ] **Step 5: Implement `app/api/routes/templates.py`**

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.templates import TemplateCreate, TemplateRead
from app.service import template_service

router = APIRouter()


@router.post("/task-templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(body: TemplateCreate, session: Session = Depends(db_session)) -> TemplateRead:
    tpl = template_service.create_template(session, body)
    return TemplateRead.model_validate(tpl)


@router.get("/task-templates")
def list_templates(session: Session = Depends(db_session)) -> dict:
    items = template_service.list_templates(session)
    return {"templates": [TemplateRead.model_validate(t).model_dump(by_alias=True) for t in items]}
```

- [ ] **Step 6: Register in `app/api/router.py`** — add under the authed section:

```python
from app.api.routes import templates  # at top
authed_router.include_router(templates.router, tags=["templates"])
```

- [ ] **Step 7: Run to verify green**

Run: `cd backend && uv run pytest tests/api/test_templates_api.py -v`
Expected: PASS.

- [ ] **Step 8: Add an auth test** — append to the same test file:

```python
def test_requires_token(monkeypatch, session):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.delenv("AEO_ALLOW_NO_AUTH", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    c = TestClient(app)
    assert c.get("/api/task-templates").status_code == 401
    assert c.get("/api/task-templates", headers={"X-AEO-Token": "secret"}).status_code == 200
    get_settings.cache_clear()
```

Run: `cd backend && uv run pytest tests/api/test_templates_api.py -v` → PASS (3 passed).

- [ ] **Step 9: Commit**

```bash
git add backend/app/service/errors.py backend/app/service/template_service.py backend/app/api/routes/templates.py backend/app/api/router.py backend/tests/api/test_templates_api.py
git commit -m "feat: templates service + routes with auth"
```

### Task 4.2: Worker service + workers routes (read + settings + delete)

**Files:** Create `backend/app/service/worker_service.py`, `backend/app/api/routes/workers.py`; modify router. Test: `backend/tests/api/test_workers_api.py`.

- [ ] **Step 1: Write failing test** — create two workers via `repo_workers` (using the overridden session), assert `GET /api/workers` returns them; `POST /api/workers/{id}/settings` with `{"enabled": false}` flips enabled; `DELETE /api/workers/{id}` removes it.

- [ ] **Step 2: Run red.** Expected 404.

- [ ] **Step 3: Implement `worker_service.py`** with `list_workers(session)`, `update_settings(session, worker_id, WorkerSettingsUpdate)` (raise `NotFoundError` if missing; apply non-None fields; commit), `delete_worker(session, worker_id)` (commit).

- [ ] **Step 4: Implement `workers.py`** routes: `GET /workers` → `{"workers": [...]}`; `POST /workers/{worker_id}/settings` → `WorkerRead`; `DELETE /workers/{worker_id}` → `{"ok": True}`. Map `ServiceError` → `HTTPException(e.status_code)` via a small try/except in each handler (or a router-level exception handler — see Step 5).

- [ ] **Step 5: Add a global exception handler in `app/main.py`** so services can raise `ServiceError` anywhere:

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from app.service.errors import ServiceError

@app.exception_handler(ServiceError)  # add inside create_app, after app creation, as app.add_exception_handler
def _svc_err(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})
```
Use `app.add_exception_handler(ServiceError, _svc_err)` form inside `create_app`.

- [ ] **Step 6: Run green.** Commit `feat: workers service + routes`.

### Task 4.3: Dataset service + route

**Files:** Create `backend/app/service/dataset_service.py`, `backend/app/api/routes/datasets.py`; modify router. Test: `backend/tests/api/test_datasets_api.py`.

- [ ] **Step 1: Write failing test** — monkeypatch `DEFAULT_PRESET_DATASETS` to point one ref at an existing tmp dir and one at a missing path; assert `GET /api/datasets` returns `available: true/false` accordingly.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `dataset_service.py`** — `list_datasets() -> list[DatasetInfo]` iterating `get_settings()`-resolved preset dataset map (port `DEFAULT_PRESET_DATASETS`; resolve each path; `available = path.is_dir()`).

- [ ] **Step 4: Implement route** `GET /datasets` → `DatasetsResponse`.

- [ ] **Step 5: Run green. Commit** `feat: datasets route`.

### Task 4.4: Dashboard service + routes

**Files:** Create `backend/app/service/dashboard_service.py`, `backend/app/api/routes/dashboard.py`; modify router. Test: `backend/tests/api/test_dashboard_api.py`.

- [ ] **Step 1: Write failing test** — seed a run + a queued batch + 2 case_runs (1 succeeded, 1 failed) via repos; assert `GET /api/dashboard/tasks` returns one task with `counts` `{"succeeded":1,"failed":1}` and a derived `status`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `dashboard_service.py`** — `list_tasks(session)`: for each run, gather its batches + case_runs, compute counts by case status and an overall status using `app.service.status._overall_status_from_batch_counts` (build the `status_counts` dict the helper expects from batch statuses). Return `list[DashboardTask]`.

- [ ] **Step 4: Implement routes** `GET /dashboard/tasks` → `DashboardTasksResponse`; `GET /dashboard/batches` → `{"batches": [...]}` (list all batches as `BatchRead`).

- [ ] **Step 5: Run green. Commit** `feat: dashboard service + routes`.

### Task 4.5: Run detail + case-runs routes

**Files:** Create `backend/app/service/run_service.py`, `backend/app/api/routes/runs.py`, `backend/app/api/routes/case_runs.py`, `backend/app/api/routes/batches.py`; modify router. Test: `backend/tests/api/test_runs_api.py`.

- [ ] **Step 1: Write failing test** — seed run+batch+cases; assert `GET /api/eval-tasks/{run_id}` returns run fields + its batches; `GET /api/case-runs?runId={run_id}` returns the cases; `GET /api/batches/{batch_id}` returns the batch.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `run_service.py`** — `get_run_detail(session, run_id)` (raise NotFound; return run + batches + case counts), `list_case_runs(session, run_id)`, plus stubs `create_and_distribute(...)` to be filled in Task 5.4 (leave a `raise NotImplementedError` is NOT allowed — instead implement the read functions only here; the write function lands in its own task).

- [ ] **Step 4: Implement routes** as per the test. Use `RunRead`/`BatchRead`/`CaseRunRead` schemas.

- [ ] **Step 5: Run green. Commit** `feat: run/batch/case-run read routes`.

---

# Phase 5 — Worker protocol, orchestration, file transfer, enroll

### Task 5.1: Worker register/heartbeat endpoints

**Files:** Create `backend/app/api/routes/worker_protocol.py`, `backend/app/service/worker_protocol_service.py`; modify router. Test: `backend/tests/api/test_worker_protocol_api.py`.

- [ ] **Step 1: Write failing test**

```python
def test_register_then_heartbeat(client):
    r = client.post("/api/workers/register", json={
        "workerId": "w1", "displayName": "W1", "host": "h", "slotsTotal": 2, "capabilities": {}})
    assert r.status_code == 200 and r.json()["workerId"] == "w1"
    hb = client.post("/api/workers/heartbeat", json={"workerId": "w1", "slotsUsed": 1, "status": "online"})
    assert hb.status_code == 200 and hb.json()["ok"] is True
```

(Reuse the `client` fixture pattern from Task 4.1.)

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `worker_protocol_service.py`** — `register(session, RegisterRequest)` calls `repo_workers.upsert_worker` + commit; `heartbeat(session, HeartbeatRequest)` updates worker runtime (`update_runtime` with slots_used/status/now_iso) and, if `batch_id` present, applies batch status/summary/cases (set batch status, `repo_case_runs.replace_for_batch` when cases provided, set summary) + commit.

- [ ] **Step 4: Implement routes** `POST /workers/register` → `RegisterResponse`; `POST /workers/heartbeat` → `HeartbeatResponse`.

- [ ] **Step 5: Run green. Commit** `feat: worker register + heartbeat endpoints`.

### Task 5.2: Asset manifest building + serving (claim asset contract)

**Files:** Create `backend/app/service/asset_service.py`, add `assets` routes to `worker_protocol.py`. Test: `backend/tests/api/test_assets_api.py`, `backend/tests/service/test_asset_service.py`.

- [ ] **Step 1: Write failing service test** — `test_asset_service.py`: seed a batch whose `executor_metadata` has `datasetPath` (a tmp dataset dir with case dirs `c1`,`c2`), `bitfunCliPath` (a tmp file), `bitfunConfigDir` (a tmp dir with one config file), and `selected_case_ids=["c1"]`. Call `build_manifest(session, batch_id)`; assert the returned `AssetManifest` has `AssetEntry` rows for the files under `c1` (kind `case`), the bitfun cli (kind `cli`), and the bitfun config file (kind `bitfun`), each with correct `sha256` (compute expected with `hashlib.sha256`), and `target_root_rel == f"sync/{run_id}"`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `asset_service.py`** (manifest is derived from the batch's persisted `executor_metadata`, set in Task 5.9):
  - `build_manifest(session, batch_id) -> AssetManifest` — load the batch via `repo_batches.get_batch`; read `meta = batch.executor_metadata`; roots = `datasetPath` (only the `selected_case_ids` subdirs), `bitfunConfigDir`, `bitfunCliPath`. Walk files under each, producing `AssetEntry(path=<rel-to-target-root>, size, sha256, kind)` with `kind` per source (`case`/`bitfun`/`cli`). `target_root_rel = f"sync/{batch.run_id}"`; `asset_manifest_id = f"am-{batch_id}"`. Persist nothing.
  - `manifest_for(session, asset_manifest_id) -> AssetManifest` — strip the `am-` prefix to recover `batch_id`, then `build_manifest`.
  - `open_entry(session, asset_manifest_id, path) -> Path` — map a manifest entry's `path` back to its absolute source file; reject traversal by asserting the resolved path is under one of the batch's metadata roots (`datasetPath`/`bitfunConfigDir`/parent of `bitfunCliPath`).

- [ ] **Step 4: Write failing API test** — `test_assets_api.py`: seed batch with case dir; `GET /api/workers/assets/am-<batch_id>` returns manifest JSON; `GET /api/workers/assets/am-<batch_id>/file?path=<entry>` streams the file bytes (assert content matches).

- [ ] **Step 5: Implement asset routes** in `worker_protocol.py`:
  - `GET /workers/assets/{asset_manifest_id}` → manifest JSON (`AssetManifest`).
  - `GET /workers/assets/{asset_manifest_id}/file` with `path` query → `fastapi.responses.FileResponse` (supports Range automatically). Validate via `asset_service.open_entry`.

- [ ] **Step 6: Run green (both tests). Commit** `feat: asset manifest service + streaming endpoints`.

### Task 5.3: Claim endpoint (returns asset contract)

**Files:** Add claim to `worker_protocol.py`; extend `worker_protocol_service.py`. Test: extend `test_worker_protocol_api.py`.

- [ ] **Step 1: Write failing test** — register worker; seed a queued batch already `assigned` to that worker (use `repo_batches.assign`); `POST /api/workers/claim {"workerId":"w1"}` returns `batchId`, `assetManifestId == f"am-{batch_id}"`, `assetUrl` ending in that id, and a non-empty `assetManifest`. A second claim with no assigned batch returns `batchId == null`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `claim(session, ClaimRequest) -> ClaimResponse`** in service — find the oldest batch with `assigned_worker_id == worker_id and status == "assigned"`; if none, return empty `ClaimResponse()`. Otherwise set batch status `"running"`, build manifest via `asset_service.build_manifest`, return `ClaimResponse(batch_id, dataset_ref=<from template/run>, executor_config=<batch.executor_metadata>, asset_manifest_id, asset_url=f"/api/workers/assets/{id}", asset_manifest=manifest)` + commit.

- [ ] **Step 4: Implement route** `POST /workers/claim` → `ClaimResponse`.

- [ ] **Step 5: Run green. Commit** `feat: claim endpoint with asset contract`.

### Task 5.4: job-archive (multipart streaming) + result merge

**Files:** Add job-archive route to `worker_protocol.py`; create `backend/app/service/orchestration/__init__.py` (empty) + `backend/app/service/orchestration/result_collector.py`. Test: `backend/tests/api/test_job_archive_api.py`.

- [ ] **Step 1: Write failing test** — build a tar in-memory of a fake harbor job dir; `POST /api/workers/job-archive` as multipart: fields `batchId`, `sha256`, file `archive` (the tar bytes). Assert 200, `batchId` echoed, and that the extracted dir exists under the controller `imported-jobs/<batchId>`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `result_collector.py`**:
  - `ingest_archive(session, *, batch_id, sha256, file_stream, layout) -> None` — stream the upload to a temp file, verify `sha256`, `tarfile` safe-extract into `layout.imported_jobs_dir / batch_id` (port `_safe_extract_tar` from `server.py`), then call the merge step (port `_rebuild_merged_job_for_run` / `_apply_exception_rerun_merge` from `server.py` — move those helpers here, rewriting imports to `app.*` and using repos instead of `Store`). For this task, the merge can be the minimal "copy trial dirs + refresh job result" using ported `normalizers/harbor_job_merge.py`.

- [ ] **Step 4: Implement route** `POST /workers/job-archive` using `UploadFile` + `Form`:

```python
from fastapi import UploadFile, File, Form

@router.post("/workers/job-archive", response_model=JobArchiveResponse)
def job_archive(batch_id: str = Form(alias="batchId"), sha256: str = Form(...),
                archive: UploadFile = File(...), session: Session = Depends(db_session)):
    from app.core.layout import default_layout
    from app.core.config import get_settings
    layout = default_layout(get_settings().shared_root)
    result_collector.ingest_archive(session, batch_id=batch_id, sha256=sha256,
                                     file_stream=archive.file, layout=layout)
    return JobArchiveResponse(batch_id=batch_id)
```

- [ ] **Step 5: Run green. Commit** `feat: multipart job-archive ingest + merge`.

### Task 5.5: Port asset_syncer (local + http transports, no SSH)

**Files:** Create `backend/app/service/orchestration/asset_syncer.py`. Test: port `tests/controller/test_asset_syncer.py` → `backend/tests/orchestration/test_asset_syncer.py` (drop ssh-specific cases).

- [ ] **Step 1: Write failing test** — port the `local` transport assertions from the existing `test_asset_syncer.py` (case copy, bitfun copy). Add one test asserting `build_sync_manifest` produces `transport="http"` (never `"ssh"`) for a non-local worker and never requires `ssh_host_alias`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Port `asset_syncer.py`** — copy `src/agent_eval_orchestrator/controller/asset_syncer.py`, rewriting imports to `app.*`, and **remove** the `SshRunner` import and all `ssh` transport branches: `build_sync_manifest` sets `transport = "local" if local else "http"`; delete `validate_create_task_assets`'s `ssh_host_alias` requirement (a non-local worker is always allowed now). Keep `sync_cases_local`, `sync_bitfun_local`, `is_local_worker`, manifest building.

- [ ] **Step 4: Run green. Commit** `feat: port asset_syncer (http transport, no ssh)`.

### Task 5.6: Port rerun coordinator + viewer manager

**Files:** Create `backend/app/service/orchestration/rerun_coordinator.py`, `backend/app/service/orchestration/viewer_manager.py`. Test: port `tests/controller/test_run_rerun_coordinator.py` and `test_global_harbor_viewer_paths.py`.

- [ ] **Step 1: Write failing tests** — port assertions, changing imports to `app.service.orchestration.*` and replacing `Store` usage with the new repos (the coordinator currently takes a `Store`; refactor its constructor to take a `Session`-providing callable `session_factory` and call repos). Keep behavior assertions identical.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Port both modules** — copy from `controller/run_rerun_coordinator.py` and `controller/harbor_viewer.py`, rewrite imports to `app.*`, replace `Store` calls with repo calls (`repo_runs`, `repo_batches`, `repo_rerun_jobs`). The viewer manager (`harbor_viewer.py`) has no DB dependency — straight port with import rewrites.

- [ ] **Step 4: Run green. Commit** `feat: port rerun coordinator + viewer manager`.

### Task 5.7: Scheduler + reaper loops

**Files:** Create `backend/app/service/orchestration/scheduler.py`, `reaper.py`. Test: `backend/tests/orchestration/test_scheduler.py`, `test_reaper.py`.

- [ ] **Step 1: Write failing test (scheduler)** — seed 2 online workers (slots 1 each, slots_used 0) and 3 queued batches; call `scheduler.assign_once(session)`; assert exactly 2 batches become `assigned` (one per free slot) and pick respects `allocation_weight` ordering. (Test the single-pass function, not the thread loop.)

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `scheduler.py`** with `assign_once(session) -> int`: load queued batches (ordered by created_at), load enabled online workers with free slots (`slots_used < slots_total`), assign greedily weighted by `allocation_weight` (port the smoothing logic from `store.py`'s allocation if present, else simple highest-weight-free-slot first); for each assignment call `repo_batches.assign` + bump in-memory slots_used; commit; return count. Also expose `run_loop(stop_event, session_factory, interval)` that calls `assign_once` in a try/except + `time.sleep`.

- [ ] **Step 4: Write failing test (reaper)** — seed a worker with `last_heartbeat_at` far in the past and a `running` batch assigned to it; call `reaper.reap_once(session, timeout_sec=45)`; assert worker status→`offline` and its running batch→`queued` (requeued) with `assigned_worker_id=None`.

- [ ] **Step 5: Implement `reaper.py`** with `reap_once(session, timeout_sec)` and `run_loop(...)` mirroring scheduler.

- [ ] **Step 6: Run green (both). Commit** `feat: scheduler + reaper orchestration loops`.

### Task 5.8: OrchestrationManager + lifespan wiring

**Files:** Create `backend/app/service/orchestration/manager.py`; modify `backend/app/main.py`. Test: `backend/tests/orchestration/test_manager.py`.

- [ ] **Step 1: Write failing test** — `OrchestrationManager` with a fake loop function that increments a counter; `start()` then `stop()`; assert counter advanced and threads joined (not alive) after stop.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `manager.py`**

```python
from __future__ import annotations

import threading
from collections.abc import Callable


class OrchestrationManager:
    def __init__(self, loops: list[Callable[[threading.Event], None]]) -> None:
        self._loops = loops
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        for loop in self._loops:
            t = threading.Thread(target=loop, args=(self._stop,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
```

- [ ] **Step 4: Wire `lifespan` in `main.py`** (non-async work inside an async wrapper):

```python
from contextlib import asynccontextmanager
from app.service.orchestration.manager import OrchestrationManager
from app.service.orchestration import scheduler, reaper
from app.model.db import get_session

@asynccontextmanager
async def lifespan(app: FastAPI):
    mgr = OrchestrationManager([
        lambda stop: scheduler.run_loop(stop, get_session, interval=5),
        lambda stop: reaper.run_loop(stop, get_session, interval=5),
    ])
    mgr.start()
    try:
        yield
    finally:
        mgr.stop()
```
Pass `lifespan=lifespan` to `FastAPI(...)` in `create_app`. **Guard**: only start loops when `AEO_DISABLE_ORCHESTRATION` env is unset, so tests using `TestClient` don't spawn threads (set this env in `conftest.py` client fixtures, or check inside lifespan).

- [ ] **Step 5: Run green + full suite.** `cd backend && uv run pytest -q` → all PASS. **Commit** `feat: orchestration manager + lifespan wiring`.

### Task 5.9: create-and-distribute (write path)

**Files:** Extend `run_service.py`; add route to `runs.py`. Test: `backend/tests/api/test_create_distribute_api.py`.

First add the request schema. In `app/schema/runs.py` add (these mirror the fields the legacy handler reads at `src/agent_eval_orchestrator/controller/server.py:793` — `datasetPath`, `bitfunCliPath`, `bitfunConfigDir`, `workerIds`, `selectedCaseIds`, `executorConfig`, `name`, `modelProfileRef`, concurrency):

```python
class CreateDistributeRequest(ApiModel):
    name: str
    owner: str = "demo"
    dataset_path: str                       # absolute path to the downloaded dataset on the controller
    bitfun_cli_path: str
    bitfun_config_dir: str
    selected_case_ids: list[str] = []       # empty → all cases under dataset_path
    worker_ids: list[str] = []              # empty → all enabled online workers
    per_worker_concurrency: int = 1
    executor_config: dict = {}
    model_profile_ref: str | None = None


class CreateDistributeResponse(ApiModel):
    run_id: str
    batch_ids: list[str]
```

- [ ] **Step 1: Write failing test** — seed 2 enabled online workers; create a tmp dataset dir with case dirs `c1`,`c2`; `POST /api/eval-tasks/create-and-distribute` with `name`, `datasetPath`, `bitfunCliPath`/`bitfunConfigDir` pointing at tmp files, `selectedCaseIds=["c1","c2"]`, `perWorkerConcurrency=1`. Assert: returns `runId` + `batchIds` (len ≥ 1); each created batch is `queued`; the union of batches' `selectedCaseIds` equals `{"c1","c2"}`; **each batch's `executorMetadata` contains `datasetPath`, `bitfunCliPath`, `bitfunConfigDir`** (so the asset manifest can be built later).

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `create_and_distribute(session, req: CreateDistributeRequest) -> CreateDistributeResponse`** in `run_service.py`:
  1. Resolve case ids: if `req.selected_case_ids` empty, enumerate child dir names under `req.dataset_path` (port `list_dataset_case_ids`).
  2. Resolve target workers: `req.worker_ids` or all enabled online workers (`repo_workers.list_workers(only_enabled=True)` filtered to `status=="online"`).
  3. Create template (`repo_templates.create_template`, `executor_kind="harbor-docker"`, `dataset_ref=req.dataset_path`) and run (`repo_runs.create_run`).
  4. **Shard** case ids across workers (round-robin into `len(workers)` shards, then sub-split each shard by `per_worker_concurrency`) — port the sharding from `server.py` create handler / `store.create_sharded_batches`.
  5. For each shard create a queued batch via `repo_batches.create_batch(..., preferred_worker_id=<worker>, selected_case_ids=<shard>, batch_root=str(layout.batch_dir(owner, run_id, batch_id)), executor_metadata={"datasetPath": req.dataset_path, "bitfunCliPath": req.bitfun_cli_path, "bitfunConfigDir": req.bitfun_config_dir, "executorConfig": req.executor_config, "modelProfileRef": req.model_profile_ref})`.
  6. Build `worker_shards = {worker_id: case_ids}` and a `sync_manifest` via the ported `asset_syncer.build_sync_manifest`; store it on the run via `repo_runs.set_sync(manifest=...)`.
  7. Set run latest batch (last created) and commit. Return run id + batch ids. The scheduler (Task 5.7) assigns these queued batches; `claim` then builds the per-batch asset manifest from `executor_metadata`.

- [ ] **Step 4: Implement route** `POST /eval-tasks/create-and-distribute`.

- [ ] **Step 5: Run green. Commit** `feat: create-and-distribute run endpoint`.

### Task 5.10: Files route + harbor-viewer route + remaining run actions

**Files:** Create `backend/app/service/files_service.py`, `backend/app/api/routes/files.py`, `backend/app/api/routes/harbor_viewer.py`; extend `runs.py` (sync, rerun, rerun-exceptions, batches/{id}/viewer). Test: `backend/tests/api/test_files_api.py`, `test_harbor_viewer_api.py`, `test_run_actions_api.py`.

- [ ] **Step 1: files** — failing test: `GET /api/files/read?path=<tmpfile>` returns its text; path traversal outside allowed roots → 400. Implement `files_service.read_file(path, allowed_roots)` (port from `server.py` `/api/files/read`, enforce roots = shared_root + harbor_repo). Route returns `{"content": ...}`. Green. Commit.

- [ ] **Step 2: harbor-viewer** — failing test: `GET /api/harbor-viewer/global` returns viewer status dict (stub the subprocess by monkeypatching `viewer_manager.ensure_global`). Implement route delegating to ported `viewer_manager`. Green. Commit.

- [ ] **Step 3: run actions** — failing tests for `POST /api/runs/{id}/rerun-exceptions` (creates a rerun job via `rerun_coordinator`) and `GET /api/runs/{id}/sync` (returns sync status). Implement routes delegating to `rerun_coordinator` + `repo_runs`. Green. Commit `feat: files, harbor-viewer, run-action routes`.

### Task 5.11: Static SPA mount (serves frontend build)

**Files:** Modify `backend/app/main.py`. Test: `backend/tests/api/test_static_mount.py`.

- [ ] **Step 1: Write failing test** — create a tmp `frontend/dist/index.html`; point an env `AEO_FRONTEND_DIST` at it; assert `GET /` returns the index html and an unknown non-`/api` path also returns index html (SPA fallback), while `GET /api/health` still returns JSON.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement SPA mount** in `create_app` (after routers):

```python
import os
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

dist = Path(os.environ.get("AEO_FRONTEND_DIST", "frontend/dist"))
if dist.is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        index = dist / "index.html"
        return FileResponse(index)
```

- [ ] **Step 4: Run green + full suite. Commit** `feat: serve SPA build with fallback`.

---

# Phase 6 — Worker rewrite, enroll script, ops scripts, cleanup

### Task 6.1: Rewrite worker daemon (HTTP asset pull + streaming upload)

**Files:** Create `backend/app/worker/__init__.py` (empty), `backend/app/worker/daemon.py`. Test: `backend/tests/worker/test_daemon_asset_pull.py`, `test_daemon_archive_upload.py`.

- [ ] **Step 1: Write failing test (asset pull)** — given an `AssetManifest` and a stub HTTP fetcher returning known bytes, `daemon.pull_assets(manifest, base_url, target_root, fetch)` writes each entry to `target_root/<path>`, verifies sha256, and retries a failing entry up to N times before raising. Assert files written + checksum verified.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement `daemon.py`** — rewrite from `src/agent_eval_orchestrator/worker/daemon.py`. Keep the poll loop shape (register → claim → if batch: pull assets → run executor → stream archive → heartbeat). Replace:
  - `post_json` stays for register/claim/heartbeat (now with the new fields).
  - **asset pull**: new `pull_assets(manifest, base_url, target_root, fetch=_http_get)` — for each entry GET `f"{base_url}/file?path={entry.path}"` with `X-AEO-Token`, write to `target_root/entry.path`, verify `hashlib.sha256`, retry ≤3 with Range resume.
  - **archive upload**: new `upload_archive(controller_url, batch_id, job_dir, token)` — tar the job dir to a temp file, compute sha256, POST multipart (`urllib` with a `multipart/form-data` body, or `http.client`) to `/api/workers/job-archive`. No base64.
  - On asset-pull failure, send heartbeat with `status="sync_failed"` for the batch and skip execution.

- [ ] **Step 4: Write failing test (archive upload)** — monkeypatch the HTTP POST to capture the multipart body; assert it contains `batchId`, `sha256`, and the tar bytes; assert no base64.

- [ ] **Step 5: Run green. Commit** `feat: rewrite worker daemon (asset pull + streaming upload)`.

### Task 6.2: Enroll service + enroll route + enroll.sh template

**Files:** Create `scripts/enroll.sh.tmpl`, `backend/app/service/enroll_service.py`, `backend/app/api/routes/enroll.py`; modify router. Test: `backend/tests/api/test_enroll_api.py`.

- [ ] **Step 1: Write failing test** — `GET /api/workers/enroll.sh?token=secret` (with `AEO_TOKEN=secret`) returns `200`, content-type `text/x-shellscript`, body contains the controller URL, the token, `uv sync`, `register`, and a `curl .../api/workers/code-bundle` line. Without token → 401.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Write `scripts/enroll.sh.tmpl`** — a bash script with `{{CONTROLLER_URL}}`, `{{TOKEN}}`, `{{WORKER_ID}}` placeholders that: installs uv (curl astral.sh) if missing; ensures Docker present (check, else print instruction); downloads code bundle from `{{CONTROLLER_URL}}/api/workers/code-bundle?token={{TOKEN}}` and extracts; `uv sync`; starts the daemon via `nohup uv run python -m app.worker.daemon --controller-url {{CONTROLLER_URL}} --worker-id {{WORKER_ID}} --auth-token {{TOKEN}} ... &`. It must NOT download datasets.

- [ ] **Step 4: Implement `enroll_service.py`** — `render_enroll_script(controller_url, token, worker_id) -> str` (read template, substitute placeholders); `build_code_bundle(repo_roots) -> bytes` (tar of project + harbor source, excluding `.git`, `runtime`, `datasets`, `node_modules`).

- [ ] **Step 5: Implement routes** `GET /workers/enroll.sh` → `PlainTextResponse(media_type="text/x-shellscript")`; `GET /workers/code-bundle` → streamed tar. Both require token (the enroll.sh uses `?token=`).

- [ ] **Step 6: Run green. Commit** `feat: enroll script service + endpoints`.

### Task 6.3: Ops scripts

**Files:** Create `scripts/start-controller.sh`, `scripts/stop-controller.sh`, `scripts/start-worker.sh`.

- [ ] **Step 1: Write `scripts/start-controller.sh`** — first **fail fast if neither `AEO_TOKEN` nor `AEO_ALLOW_NO_AUTH=1` is set** (since the controller binds a network-reachable host, refuse to launch wide open):

```bash
if [ -z "${AEO_TOKEN:-}" ] && [ "${AEO_ALLOW_NO_AUTH:-}" != "1" ]; then
  echo "refusing to start: set AEO_TOKEN (or AEO_ALLOW_NO_AUTH=1 for local dev)" >&2
  exit 1
fi
```

Then run `uv run alembic upgrade head`, then `setsid uv run uvicorn app.main:app --host "${AEO_HOST:-0.0.0.0}" --port "${AEO_PORT:-8790}"` with log redirection to `runtime/logs/`, writing a pidfile. Working dir `backend/`.

- [ ] **Step 2: Write `scripts/stop-controller.sh`** — reads pidfile, `kill` the process, removes pidfile.

- [ ] **Step 3: Write `scripts/start-worker.sh`** — convenience wrapper around `uv run python -m app.worker.daemon` taking env vars for controller url/worker id/token/slots.

- [ ] **Step 4: Make executable + smoke check** — `chmod +x scripts/*.sh`; `bash -n scripts/*.sh` (syntax check) returns 0.

- [ ] **Step 5: Commit** `feat: ops scripts for controller/worker lifecycle`.

### Task 6.4: Delete legacy tree + update README

**Files:** Delete `src/agent_eval_orchestrator/`, old root `alembic/`, `tests/` (old), `pyproject.toml` (root), `build/`, `dist/`. Modify: root `README.md`.

- [ ] **Step 1: Confirm new suite is green** — `cd backend && uv run pytest -q` → all PASS. Only proceed if green.

- [ ] **Step 2: Delete legacy backend** — only `src/agent_eval_orchestrator`, root `tests`, `pyproject.toml`, `uv.lock` are git-tracked; `alembic/`, `build/`, `dist/` are untracked/ignored. Remove tracked paths with git, untracked ones with `rm` (use `--ignore-unmatch` so a missing path never aborts the command). Keep `backend/`, `frontend/`, `scripts/`, `docs/`:

```bash
git rm -r --ignore-unmatch src/agent_eval_orchestrator tests pyproject.toml uv.lock
rm -rf alembic build dist src
```

Verify nothing under `backend/` was touched: `git status --porcelain backend | head`.

- [ ] **Step 3: Rewrite root `README.md`** — new quickstart: `cd backend && uv run alembic upgrade head`, `uv run uvicorn app.main:app --host 0.0.0.0 --port 8790`; dataset prep section (unchanged paths); add machine via the "添加机器" button / `curl .../enroll.sh`; `cd frontend && pnpm install && pnpm build`. Remove all SSH tunnel / provision instructions.

- [ ] **Step 4: Run full suite once more.** `cd backend && uv run pytest -q` → PASS.

- [ ] **Step 5: Commit** `chore: remove legacy http.server backend; update README`.

---

# Phase 7 — Frontend (Vite + React SPA)

### Task 7.1: Scaffold Vite React TS app with pnpm

**Files:** Create `frontend/` (package.json, vite.config.ts, tsconfig.json, index.html, app/main.tsx, app/root.tsx, app/app.css).

- [ ] **Step 1: Scaffold** — `cd frontend && pnpm create vite@latest . --template react-ts` (accept overwrite into empty dir), then `pnpm install`.

- [ ] **Step 2: Add deps** — `pnpm add react-router @tanstack/react-query @tanstack/react-table lucide-react sonner clsx tailwind-merge class-variance-authority zod nuqs` and `pnpm add -D tailwindcss @tailwindcss/vite`.

- [ ] **Step 3: Configure Vite** — `vite.config.ts` with `@tailwindcss/vite` plugin and a dev proxy:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8790" } },
  build: { outDir: "dist" },
});
```

- [ ] **Step 4: Verify dev build** — `pnpm build` produces `frontend/dist/index.html`.

- [ ] **Step 5: Commit** `feat: scaffold Vite React SPA`.

### Task 7.2: API client + token handling + types

**Files:** Create `frontend/app/lib/api.ts`, `frontend/app/lib/types.ts`, `frontend/app/lib/query.ts`.

- [ ] **Step 1: Implement `api.ts`** — `apiFetch(path, init?)` that reads `token` from `localStorage`/URL `?token=`, sends `X-AEO-Token`, throws on non-2xx. Helpers `getJSON`, `postJSON`.

- [ ] **Step 2: Implement `types.ts`** — TS interfaces mirroring the camelCase response schemas (Worker, Template, Run, Batch, CaseRun, DashboardTask, DatasetInfo).

- [ ] **Step 3: Implement `query.ts`** — a configured `QueryClient` with sane polling defaults (`refetchInterval` opt-in per query).

- [ ] **Step 4: Commit** `feat: frontend api client + types`.

### Task 7.3: shadcn/ui base + app shell + routing

**Files:** Create `frontend/components.json`, `frontend/app/components/ui/*` (button, card, table, badge, input, dialog, select, tabs — via `pnpm dlx shadcn@latest add`), `frontend/app/root.tsx` (layout + nav + Toaster + QueryClientProvider), `frontend/app/routes.tsx` (createBrowserRouter).

- [ ] **Step 1: Init shadcn** — `pnpm dlx shadcn@latest init` (Tailwind v4, neutral), then `add button card table badge input dialog select tabs sonner`.

- [ ] **Step 2: Build app shell** — `root.tsx` with `QueryClientProvider`, top nav (Tasks / Create / Workers), `<Outlet/>`, `<Toaster/>`.

- [ ] **Step 3: Define routes** — `createBrowserRouter` with paths `/` (tasks), `/create`, `/tasks/:runId`, `/workers`. Mount in `main.tsx`.

- [ ] **Step 4: Verify build** — `pnpm build` succeeds.

- [ ] **Step 5: Commit** `feat: app shell, shadcn ui, routing`.

### Task 7.4: Tasks dashboard page

**Files:** Create `frontend/app/routes/tasks.tsx`, `frontend/app/components/tasks-table.tsx`.

- [ ] **Step 1: Implement** — `useQuery(["dashboard-tasks"], () => getJSON("/api/dashboard/tasks"), {refetchInterval: 5000})`; render a TanStack Table of tasks (name, status badge, counts, updated). Row click → `/tasks/:runId`.

- [ ] **Step 2: Manual verify** — `pnpm dev`, with controller running + seeded data, the table shows tasks and polls.

- [ ] **Step 3: Commit** `feat: tasks dashboard page`.

### Task 7.5: Create-task page

**Files:** Create `frontend/app/routes/create.tsx`.

- [ ] **Step 1: Implement** — form: name, dataset select (from `/api/datasets`, disabling unavailable), case selection, per-worker concurrency; submit → `POST /api/eval-tasks/create-and-distribute`; on success toast + navigate to `/tasks/:runId`.

- [ ] **Step 2: Manual verify** — creating a task returns a runId and appears on the dashboard.

- [ ] **Step 3: Commit** `feat: create-task page`.

### Task 7.6: Task-detail page

**Files:** Create `frontend/app/routes/task-detail.tsx`, `frontend/app/components/cases-table.tsx`.

- [ ] **Step 1: Implement** — `useQuery` on `/api/eval-tasks/:runId` + `/api/case-runs?runId=`; show batches + a TanStack Table of cases (status, score, error). Buttons: open harbor-viewer, rerun-exceptions (`POST /api/runs/:id/rerun-exceptions`).

- [ ] **Step 2: Manual verify** — detail renders for a seeded run.

- [ ] **Step 3: Commit** `feat: task-detail page`.

### Task 7.7: Workers page (with "Add machine")

**Files:** Create `frontend/app/routes/workers.tsx`, `frontend/app/components/add-machine-dialog.tsx`.

- [ ] **Step 1: Implement workers list** — `useQuery` `/api/workers` (poll 5s); table with status, slots used/total, enabled toggle (`POST /api/workers/:id/settings`), delete (`DELETE /api/workers/:id`).

- [ ] **Step 2: Implement "添加机器" dialog** — shows the one-liner `curl -fsSL <origin>/api/workers/enroll.sh?token=<token> | bash` with a copy button (build the URL from `window.location.origin` + stored token).

- [ ] **Step 3: Manual verify** — toggling enabled + delete work; the copy button yields a runnable command.

- [ ] **Step 4: Commit** `feat: workers page with add-machine dialog`.

### Task 7.8: Production wiring (FastAPI serves build)

- [ ] **Step 1: Build** — `cd frontend && pnpm build` → `frontend/dist`.

- [ ] **Step 2: Run controller pointing at build** — `cd backend && AEO_FRONTEND_DIST=../frontend/dist uv run uvicorn app.main:app --port 8790`; open `http://127.0.0.1:8790/?token=<token>`; verify SPA loads and talks to the API on the same origin.

- [ ] **Step 3: Document** in README the `pnpm build` + `AEO_FRONTEND_DIST` step.

- [ ] **Step 4: Commit** `docs: document SPA build + single-port serving`.

---

## Final verification

- [ ] `cd backend && uv run pytest -q` → all PASS.
- [ ] `cd backend && DATABASE_URL=sqlite:///$(pwd)/.smoke.db uv run alembic upgrade head` → exit 0; `rm .smoke.db*`.
- [ ] `cd frontend && pnpm build` → `dist/index.html` exists.
- [ ] End-to-end smoke: start controller (with `AEO_TOKEN`), enroll a local worker via `enroll.sh`, create a task, watch it move queued→assigned→running→finished, confirm results merged and visible in the SPA.
