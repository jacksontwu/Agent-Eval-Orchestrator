# Worker Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual worker code update flow — SSH `git pull` on AEO/Harbor repos, `uv sync`, and daemon restart — exposed via async job API and Dashboard UI.

**Architecture:** New `worker_update_jobs` table and `WorkerUpdater` class mirror the existing `Provisioner` / `provision_jobs` pattern. Refactor `build_daemon_start_command()` for dynamic paths derived from `capabilities.sharedRoot`. Reuse `Provisioner.decommission_worker()`, `_establish_tunnel()`, and `_wait_for_register()` for stop/restart.

**Tech Stack:** Python 3.10+, stdlib (`http.server`, `sqlite3`, `threading`), SQLite via `Store`, embedded HTML/JS dashboard, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/controller/provisioner.py` | Refactor `build_daemon_start_command()` to accept dynamic `aeo_dir`, `uv_bin`, `log_dir` |
| `src/agent_eval_orchestrator/controller/worker_updater.py` | **New** — `WorkerUpdater` async job runner (validate → stop → pull → sync → restart → wait) |
| `src/agent_eval_orchestrator/storage/store.py` | `worker_update_jobs` table + CRUD; `last_update_job_id` in `_decorate_worker`; delete cleanup |
| `src/agent_eval_orchestrator/controller/server.py` | POST/GET/cancel update routes; wire `WorkerUpdater` in `main()`; delete cancels active update |
| `src/agent_eval_orchestrator/controller/static.py` | Update button, confirmation modal, progress polling |
| `tests/controller/test_build_daemon_start_command.py` | **New** — unit tests for dynamic start command |
| `tests/storage/test_worker_update_store.py` | **New** — store CRUD tests |
| `tests/controller/test_worker_updater.py` | **New** — updater step generation + run_job with mocked SSH |
| `tests/controller/test_update_worker_api.py` | **New** — HTTP integration tests |

---

### Task 1: Dynamic `build_daemon_start_command`

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py:48-72`
- Create: `tests/controller/test_build_daemon_start_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_build_daemon_start_command.py`:

```python
from agent_eval_orchestrator.controller.provisioner import (
    DEFAULT_AEO_DIR,
    DEFAULT_UV_BIN,
    DEFAULT_WORKER_LOG_DIR,
    build_daemon_start_command,
)


def test_build_daemon_start_command_uses_defaults():
    cmd = build_daemon_start_command(
        worker_id="w1",
        display_name="Worker One",
        slots=2,
        controller_url="http://192.168.0.211:7380",
        auth_token="secret",
    )
    assert f"cd {DEFAULT_AEO_DIR}" in cmd
    assert DEFAULT_UV_BIN in cmd
    assert DEFAULT_WORKER_LOG_DIR in cmd
    assert '--worker-id "w1"' in cmd
    assert f"--shared-root {DEFAULT_AEO_DIR}/runtime" in cmd


def test_build_daemon_start_command_dynamic_paths():
    cmd = build_daemon_start_command(
        worker_id="w2",
        display_name="Worker Two",
        slots=1,
        controller_url="http://127.0.0.1:17380",
        auth_token="tok",
        aeo_dir="/home/djn/worker/agent-eval-orchestrator",
        uv_bin="/home/djn/.local/bin/uv",
        log_dir="/home/djn/worker/logs",
    )
    assert "cd /home/djn/worker/agent-eval-orchestrator" in cmd
    assert "/home/djn/.local/bin/uv run python" in cmd
    assert "/home/djn/worker/logs/daemon-w2.log" in cmd
    assert "--shared-root /home/djn/worker/agent-eval-orchestrator/runtime" in cmd
    assert "DEFAULT_AEO_DIR" not in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --extra dev pytest tests/controller/test_build_daemon_start_command.py -v`
Expected: FAIL — `build_daemon_start_command()` got unexpected keyword argument `aeo_dir`

- [ ] **Step 3: Write minimal implementation**

Replace `build_daemon_start_command` in `src/agent_eval_orchestrator/controller/provisioner.py`:

```python
def build_daemon_start_command(
    *,
    worker_id: str,
    display_name: str,
    slots: int,
    controller_url: str,
    auth_token: str,
    aeo_dir: str | None = None,
    uv_bin: str | None = None,
    log_dir: str | None = None,
) -> str:
    aeo = aeo_dir or DEFAULT_AEO_DIR
    uv = uv_bin or DEFAULT_UV_BIN
    logs = log_dir or DEFAULT_WORKER_LOG_DIR
    local_root = f"{aeo}/runtime/workers/{worker_id}/local"
    log_path = f"{logs}/daemon-{worker_id}.log"
    return (
        f"( mkdir -p {logs} && "
        f"cd {aeo} && "
        f"setsid {uv} run python -u -m agent_eval_orchestrator.worker.daemon "
        f'--controller-url "{controller_url}" '
        f'--worker-id "{worker_id}" '
        f'--display-name "{display_name}" '
        f'--host "$(hostname -f || hostname)" '
        f"--shared-root {aeo}/runtime "
        f'--local-root "{local_root}" '
        f"--slots {slots} "
        f"--poll-interval 3 "
        f'--auth-token "{auth_token}" '
        f'>> "{log_path}" 2>&1 < /dev/null & )'
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_build_daemon_start_command.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_build_daemon_start_command.py
git commit -m "refactor: allow dynamic paths in build_daemon_start_command"
```

---

### Task 2: `worker_update_jobs` store layer

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py:167-182` (schema block), `:698-708` (delete_worker), `:1579-1619` (_decorate_worker)
- Create: `tests/storage/test_worker_update_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_worker_update_store.py`:

```python
from agent_eval_orchestrator.core.ids import new_id


def _sample_steps():
    return [
        {"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"},
        {"id": "stop_daemon", "label": "停止 Worker Daemon", "status": "pending"},
    ]


def test_worker_update_job_crud(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    job = store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        steps=_sample_steps(),
    )
    assert job["status"] == "pending"
    assert job["targets"] == ["aeo", "harbor"]

    store.append_worker_update_log(job_id, "pull output\n")
    updated = store.update_worker_update_job(
        job_id,
        status="running",
        current_step="validate_ssh",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "running"}],
    )
    assert updated is not None
    assert updated["log_text"].endswith("pull output\n")

    fetched = store.get_worker_update_job(job_id)
    assert fetched is not None
    assert fetched["worker_id"] == "ecs-worker-upd"


def test_get_active_worker_update_job(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=_sample_steps(),
    )
    active = store.get_active_worker_update_job_for_worker("ecs-worker-upd")
    assert active is not None
    assert active["job_id"] == job_id

    store.update_worker_update_job(job_id, status="succeeded", finished=True)
    assert store.get_active_worker_update_job_for_worker("ecs-worker-upd") is None


def test_delete_worker_removes_update_jobs(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=_sample_steps(),
    )
    assert store.delete_worker("ecs-worker-upd") is True
    assert store.get_worker_update_job(job_id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_worker_update_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'create_worker_update_job'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, add table creation after `provision_jobs` block (~line 182):

```python
                CREATE TABLE IF NOT EXISTS worker_update_jobs (
                    job_id       TEXT PRIMARY KEY,
                    worker_id    TEXT NOT NULL,
                    targets_json TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    current_step TEXT,
                    steps_json   TEXT NOT NULL,
                    log_text     TEXT NOT NULL DEFAULT '',
                    error_text   TEXT,
                    created_at   TEXT NOT NULL,
                    finished_at  TEXT
                );
```

Add methods after `update_provision_job` / `_provision_job_item` (~line 894):

```python
    def create_worker_update_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        targets: list[str],
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_update_jobs(
                    job_id, worker_id, targets_json, status, current_step,
                    steps_json, log_text, error_text, created_at, finished_at
                ) VALUES(?, ?, ?, 'pending', NULL, ?, '', NULL, ?, NULL)
                """,
                (
                    job_id,
                    worker_id,
                    json.dumps(targets, ensure_ascii=False),
                    json.dumps(steps, ensure_ascii=False),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(row)

    def get_worker_update_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(row) if row else None

    def get_latest_worker_update_job_for_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM worker_update_jobs
                WHERE worker_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (worker_id,),
            ).fetchone()
        return self._worker_update_job_item(row) if row else None

    def get_active_worker_update_job_for_worker(self, worker_id: str) -> dict[str, Any] | None:
        latest = self.get_latest_worker_update_job_for_worker(worker_id)
        if not latest:
            return None
        if str(latest["status"]) in {"pending", "running"}:
            return latest
        return None

    def append_worker_update_log(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE worker_update_jobs
                SET log_text = log_text || ?
                WHERE job_id = ?
                """,
                (chunk, job_id),
            )

    def update_worker_update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        error_text: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            next_status = status or str(row["status"])
            next_step = current_step if current_step is not None else row["current_step"]
            next_steps_json = (
                json.dumps(steps, ensure_ascii=False)
                if steps is not None
                else str(row["steps_json"])
            )
            next_error = error_text if error_text is not None else row["error_text"]
            finished_at = now if finished else row["finished_at"]
            conn.execute(
                """
                UPDATE worker_update_jobs
                SET status = ?, current_step = ?, steps_json = ?,
                    error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_step, next_steps_json, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM worker_update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._worker_update_job_item(updated)

    def _worker_update_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["targets"] = json.loads(item.pop("targets_json"))
        item["steps"] = json.loads(item.pop("steps_json"))
        item["log_tail"] = item["log_text"][-8192:] if item.get("log_text") else ""
        return item
```

Update `delete_worker` to also delete update jobs:

```python
            conn.execute("DELETE FROM worker_update_jobs WHERE worker_id = ?", (worker_id,))
            conn.execute("DELETE FROM provision_jobs WHERE worker_id = ?", (worker_id,))
```

In `_decorate_worker`, before each `return item`, add (after provision job lookup):

```python
        latest_update = self.get_latest_worker_update_job_for_worker(str(item["worker_id"]))
        if latest_update:
            item["last_update_job_id"] = latest_update["job_id"]
            if str(latest_update["status"]) in {"pending", "running"}:
                item["update_status"] = "updating"
```

Apply the `latest_update` block in all three return paths inside `_decorate_worker` (provisioning, failed, normal).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_worker_update_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_worker_update_store.py
git commit -m "feat: add worker_update_jobs store layer"
```

---

### Task 3: `WorkerUpdater` step generation and path resolution

**Files:**
- Create: `src/agent_eval_orchestrator/controller/worker_updater.py`
- Create: `tests/controller/test_worker_updater.py` (first half — step/path tests only)

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_worker_updater.py` with step/path tests:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater


@pytest.fixture()
def updater(store, sample_ssh_config, tmp_path):
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    return WorkerUpdater(
        store=store,
        ssh_config_path=sample_ssh_config,
        auth_token="test-token",
        controller_port=8790,
        provisioner=provisioner,
    )


def test_initial_steps_both_targets(updater):
    steps = updater.initial_steps(["aeo", "harbor"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_aeo",
        "sync_aeo",
        "pull_harbor",
        "restart_daemon",
        "wait_register",
    ]


def test_initial_steps_aeo_only(updater):
    steps = updater.initial_steps(["aeo"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_aeo",
        "sync_aeo",
        "restart_daemon",
        "wait_register",
    ]
    assert "pull_harbor" not in ids


def test_initial_steps_harbor_only(updater):
    steps = updater.initial_steps(["harbor"])
    ids = [step["id"] for step in steps]
    assert ids == [
        "validate_ssh",
        "stop_daemon",
        "pull_harbor",
        "restart_daemon",
        "wait_register",
    ]
    assert "pull_aeo" not in ids
    assert "sync_aeo" not in ids


def test_resolve_paths_from_shared_root(updater):
    worker = {
        "capabilities": {
            "sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime",
        }
    }
    paths = updater.resolve_paths(worker)
    assert paths["aeo_dir"] == "/home/djn/worker/agent-eval-orchestrator"
    assert paths["harbor_dir"] == "/home/djn/worker/harbor"
    assert paths["uv_bin"] == "/home/djn/.local/bin/uv"
    assert paths["shared_root"] == "/home/djn/worker/agent-eval-orchestrator/runtime"
    assert paths["log_dir"] == "/home/djn/worker/logs"


def test_resolve_paths_missing_shared_root(updater):
    with pytest.raises(RuntimeError, match="sharedRoot"):
        updater.resolve_paths({"capabilities": {}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_worker_updater.py::test_initial_steps_both_targets -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.worker_updater'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/worker_updater.py`:

```python
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.provisioner import Provisioner
    from agent_eval_orchestrator.storage.store import Store

from agent_eval_orchestrator.controller.provisioner import (
    DEFAULT_TUNNEL_REMOTE_PORT,
    DEFAULT_UV_BIN,
    DEFAULT_WORKER_LOG_DIR,
    build_daemon_start_command,
    redact_sensitive_log,
    set_step_status,
)
from agent_eval_orchestrator.controller.ssh_runner import SshRunner
from agent_eval_orchestrator.core.worker_paths import (
    default_harbor_repo_from_shared_root,
    default_uv_binary_from_shared_root,
    repo_root_from_shared_root,
    workspace_root_from_shared_root,
)

UPDATE_STEP_LABELS = {
    "validate_ssh": "校验 SSH 连接",
    "stop_daemon": "停止 Worker Daemon",
    "pull_aeo": "更新 AEO 代码",
    "sync_aeo": "同步 AEO 依赖 (uv sync)",
    "pull_harbor": "更新 Harbor 代码",
    "restart_daemon": "重启 Worker Daemon",
    "wait_register": "等待 Worker 注册",
}

ALWAYS_STEP_IDS = ["validate_ssh", "stop_daemon"]
TAIL_STEP_IDS = ["restart_daemon", "wait_register"]


def initial_update_step_ids(targets: list[str]) -> list[str]:
    ids = list(ALWAYS_STEP_IDS)
    if "aeo" in targets:
        ids.extend(["pull_aeo", "sync_aeo"])
    if "harbor" in targets:
        ids.append("pull_harbor")
    ids.extend(TAIL_STEP_IDS)
    return ids


class WorkerUpdater:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        auth_token: str,
        controller_port: int,
        provisioner: Provisioner,
    ) -> None:
        self.store = store
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self.auth_token = auth_token
        self.controller_port = controller_port
        self.provisioner = provisioner
        self.ssh = SshRunner(self.ssh_config_path, log_fn=self._log)
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()
        self._current_job_id = ""

    def initial_steps(self, targets: list[str]) -> list[dict[str, str]]:
        return [
            {"id": step_id, "label": UPDATE_STEP_LABELS[step_id], "status": "pending"}
            for step_id in initial_update_step_ids(targets)
        ]

    def resolve_paths(self, worker: dict[str, Any]) -> dict[str, str]:
        capabilities = worker.get("capabilities") or {}
        shared_root = str(capabilities.get("sharedRoot") or "").strip()
        if not shared_root:
            raise RuntimeError("worker capabilities.sharedRoot is missing")

        aeo_repo = repo_root_from_shared_root(shared_root)
        if not aeo_repo:
            raise RuntimeError(f"cannot derive aeo repo from sharedRoot: {shared_root}")

        harbor_repo = default_harbor_repo_from_shared_root(shared_root)
        uv_bin = default_uv_binary_from_shared_root(shared_root)
        workspace = workspace_root_from_shared_root(shared_root)
        log_dir = str(workspace / "logs") if workspace else DEFAULT_WORKER_LOG_DIR

        return {
            "aeo_dir": str(aeo_repo),
            "harbor_dir": str(harbor_repo) if harbor_repo else "",
            "uv_bin": str(uv_bin) if uv_bin else DEFAULT_UV_BIN,
            "shared_root": str(Path(shared_root).expanduser()),
            "log_dir": log_dir,
        }

    def start_job_async(self, **kwargs: Any) -> None:
        job_id = str(kwargs["job_id"])
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        self._threads[job_id] = thread
        thread.start()

    def cancel_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        ssh_host_alias: str,
        connection_mode: str = "tunnel",
    ) -> None:
        self._cancelled.add(job_id)
        self.provisioner.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias or None,
            connection_mode=connection_mode,
        )
        self.store.update_worker_update_job(job_id, status="cancelled", finished=True)

    def run_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        targets: list[str],
        ssh_host_alias: str,
        connection_mode: str,
        controller_internal_ip: str | None,
        tunnel_remote_port: int | None,
        display_name: str,
        slots_total: int,
        worker: dict[str, Any],
    ) -> None:
        raise NotImplementedError("implemented in Task 4")

    def _log(self, chunk: str) -> None:
        if self._current_job_id:
            self.store.append_worker_update_log(self._current_job_id, redact_sensitive_log(chunk))

    def _run_step(
        self,
        job_id: str,
        steps: list[dict[str, str]],
        step_id: str,
        fn: Callable[[], None],
    ) -> list[dict[str, str]]:
        if job_id in self._cancelled:
            raise RuntimeError("update job cancelled")
        steps = set_step_status(steps, step_id, "running")
        self.store.update_worker_update_job(job_id, current_step=step_id, steps=steps)
        fn()
        return set_step_status(steps, step_id, "succeeded")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_worker_updater.py -k "initial_steps or resolve_paths" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/worker_updater.py tests/controller/test_worker_updater.py
git commit -m "feat: add WorkerUpdater step generation and path resolution"
```

---

### Task 4: `WorkerUpdater.run_job` implementation

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/worker_updater.py` (implement `run_job`)
- Modify: `tests/controller/test_worker_updater.py` (append run_job tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_worker_updater.py`:

```python
from agent_eval_orchestrator.core.ids import new_id


def _seed_updatable_worker(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
    )
    store.set_worker_provision_status("ecs-worker-upd", provision_status="ready")
    store.register_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        host="worker-host",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
    )


def test_run_job_success(updater, store, monkeypatch):
    ssh_commands: list[str] = []

    def fake_ssh_run(alias, remote, **kwargs):
        ssh_commands.append(remote)
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Already up to date.\n"
        result.stderr = ""
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(updater.provisioner, "decommission_worker", lambda **kwargs: {"remoteCleanup": "done", "warnings": []})
    monkeypatch.setattr(updater.provisioner, "_wait_for_register", lambda *args, **kwargs: None)

    _seed_updatable_worker(store)
    worker = next(item for item in store.list_workers() if item["worker_id"] == "ecs-worker-upd")
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        steps=updater.initial_steps(["aeo", "harbor"]),
    )

    updater.run_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        ssh_host_alias="aeo-ecs-0004",
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
        display_name="ecs-worker-upd",
        slots_total=1,
        worker=worker,
    )

    joined = "\n".join(ssh_commands)
    assert "git pull" in joined
    assert "uv sync" in joined or ".local/bin/uv sync" in joined
    assert "agent_eval_orchestrator.worker.daemon" in joined
    job = store.get_worker_update_job(job_id)
    assert job["status"] == "succeeded"


def test_run_job_git_pull_failure(updater, store, monkeypatch):
    ssh_commands: list[str] = []

    def fake_ssh_run(alias, remote, **kwargs):
        ssh_commands.append(remote)
        result = MagicMock()
        if "git pull" in remote:
            result.returncode = 1
            result.stdout = ""
            result.stderr = "CONFLICT"
        else:
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
        return result

    monkeypatch.setattr(updater.ssh, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        "agent_eval_orchestrator.controller.ssh_config.test_ssh_alias",
        lambda *args, **kwargs: (True, "connected"),
    )
    monkeypatch.setattr(updater.provisioner, "decommission_worker", lambda **kwargs: {"remoteCleanup": "done", "warnings": []})

    _seed_updatable_worker(store)
    worker = next(item for item in store.list_workers() if item["worker_id"] == "ecs-worker-upd")
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=updater.initial_steps(["aeo"]),
    )

    updater.run_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        ssh_host_alias="aeo-ecs-0004",
        connection_mode="direct",
        controller_internal_ip="192.168.0.211",
        tunnel_remote_port=None,
        display_name="ecs-worker-upd",
        slots_total=1,
        worker=worker,
    )

    job = store.get_worker_update_job(job_id)
    assert job["status"] == "failed"
    assert "CONFLICT" in (job["error_text"] or "")
    assert not any("worker.daemon" in cmd for cmd in ssh_commands)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_worker_updater.py::test_run_job_success -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

Replace `run_job` in `worker_updater.py`:

```python
    def run_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        targets: list[str],
        ssh_host_alias: str,
        connection_mode: str,
        controller_internal_ip: str | None,
        tunnel_remote_port: int | None,
        display_name: str,
        slots_total: int,
        worker: dict[str, Any],
    ) -> None:
        self._current_job_id = job_id
        steps = self.initial_steps(targets)
        self.store.update_worker_update_job(job_id, status="running", steps=steps)
        paths = self.resolve_paths(worker)

        try:
            steps = self._run_step(
                job_id,
                steps,
                "validate_ssh",
                lambda: self._validate_ssh(ssh_host_alias),
            )
            steps = self._run_step(
                job_id,
                steps,
                "stop_daemon",
                lambda: self._stop_daemon(
                    worker_id=worker_id,
                    ssh_host_alias=ssh_host_alias,
                    connection_mode=connection_mode,
                ),
            )
            if "aeo" in targets:
                steps = self._run_step(
                    job_id,
                    steps,
                    "pull_aeo",
                    lambda: self._git_pull(ssh_host_alias, paths["aeo_dir"]),
                )
                steps = self._run_step(
                    job_id,
                    steps,
                    "sync_aeo",
                    lambda: self._uv_sync(ssh_host_alias, paths["aeo_dir"], paths["uv_bin"]),
                )
            if "harbor" in targets:
                harbor_dir = paths["harbor_dir"]
                if not harbor_dir:
                    raise RuntimeError("cannot derive harbor repo from sharedRoot")
                steps = self._run_step(
                    job_id,
                    steps,
                    "pull_harbor",
                    lambda: self._git_pull(ssh_host_alias, harbor_dir),
                )
            if connection_mode == "tunnel":
                steps = self._run_step(
                    job_id,
                    steps,
                    "restart_daemon",
                    lambda: self._ensure_tunnel_and_restart(
                        worker_id=worker_id,
                        ssh_host_alias=ssh_host_alias,
                        tunnel_remote_port=tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT,
                        display_name=display_name,
                        slots_total=slots_total,
                        controller_url=f"http://127.0.0.1:{tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT}",
                        paths=paths,
                    ),
                )
            else:
                controller_url = f"http://{controller_internal_ip}:{self.controller_port}"
                steps = self._run_step(
                    job_id,
                    steps,
                    "restart_daemon",
                    lambda: self._restart_daemon(
                        ssh_host_alias=ssh_host_alias,
                        worker_id=worker_id,
                        display_name=display_name,
                        slots_total=slots_total,
                        controller_url=controller_url,
                        paths=paths,
                    ),
                )
            steps = self._run_step(
                job_id,
                steps,
                "wait_register",
                lambda: self.provisioner._wait_for_register(worker_id),
            )
            self.store.update_worker_update_job(job_id, status="succeeded", steps=steps, finished=True)
        except Exception as exc:
            self.store.update_worker_update_job(
                job_id,
                status="cancelled" if job_id in self._cancelled else "failed",
                steps=steps,
                error_text=str(exc),
                finished=True,
            )

    def _validate_ssh(self, ssh_host_alias: str) -> None:
        from agent_eval_orchestrator.controller.ssh_config import test_ssh_alias

        ok, message = test_ssh_alias(self.ssh_config_path, ssh_host_alias)
        if not ok:
            raise RuntimeError(message)

    def _stop_daemon(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str,
        connection_mode: str,
    ) -> None:
        result = self.provisioner.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias,
            connection_mode=connection_mode,
        )
        warnings = result.get("warnings") or []
        if warnings:
            self._log("\n".join(str(item) for item in warnings) + "\n")

    def _git_pull(self, ssh_host_alias: str, repo_dir: str) -> None:
        result = self.ssh.ssh_run(ssh_host_alias, f"cd {repo_dir} && git pull")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"git pull failed in {repo_dir}: {detail}")

    def _uv_sync(self, ssh_host_alias: str, aeo_dir: str, uv_bin: str) -> None:
        result = self.ssh.ssh_run(ssh_host_alias, f"cd {aeo_dir} && {uv_bin} sync")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"uv sync failed in {aeo_dir}: {detail}")

    def _restart_daemon(
        self,
        *,
        ssh_host_alias: str,
        worker_id: str,
        display_name: str,
        slots_total: int,
        controller_url: str,
        paths: dict[str, str],
    ) -> None:
        remote = build_daemon_start_command(
            worker_id=worker_id,
            display_name=display_name,
            slots=slots_total,
            controller_url=controller_url,
            auth_token=self.auth_token,
            aeo_dir=paths["aeo_dir"],
            uv_bin=paths["uv_bin"],
            log_dir=paths["log_dir"],
        )
        self.ssh.ssh_run(ssh_host_alias, remote, detach=True)

    def _ensure_tunnel_and_restart(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str,
        tunnel_remote_port: int,
        display_name: str,
        slots_total: int,
        controller_url: str,
        paths: dict[str, str],
    ) -> None:
        if not self.provisioner.tunnels.get_record(worker_id):
            self.provisioner._establish_tunnel(worker_id, ssh_host_alias, tunnel_remote_port)
        self._restart_daemon(
            ssh_host_alias=ssh_host_alias,
            worker_id=worker_id,
            display_name=display_name,
            slots_total=slots_total,
            controller_url=controller_url,
            paths=paths,
        )
```

Note: tunnel-mode `restart_daemon` step uses `_ensure_tunnel_and_restart` as a single step (tunnel re-establish + start). Step list does not add a separate `establish_tunnel` step — tunnel recovery is internal to `restart_daemon`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_worker_updater.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/worker_updater.py tests/controller/test_worker_updater.py
git commit -m "feat: implement WorkerUpdater.run_job with git pull and restart"
```

---

### Task 5: Update worker API endpoints

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py` (Handler class + routes)
- Create: `tests/controller/test_update_worker_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_update_worker_api.py`:

```python
import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater
from agent_eval_orchestrator.core.ids import new_id
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, ssh_config: Path, port: int) -> ThreadedServer:
    bootstrap = ssh_config.parent / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    worker_updater = WorkerUpdater(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        provisioner=provisioner,
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.provisioner = provisioner
    Handler.worker_updater = worker_updater
    Handler.ssh_config_path = ssh_config
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def post_update(port: int, worker_id: str, body: dict | None = None) -> tuple[int, dict]:
    payload = json.dumps(body or {}).encode("utf-8")
    conn = HTTPConnection("127.0.0.1", port)
    conn.request(
        "POST",
        f"/api/workers/{worker_id}/update",
        body=payload,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode("utf-8"))


def _seed_worker(store, *, ssh: bool = True):
    if ssh:
        store.create_provisioning_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            slots_total=1,
            ssh_host_alias="aeo-ecs-0004",
            ssh_bootstrap_host_alias=None,
            connection_mode="direct",
            controller_internal_ip="192.168.0.211",
            tunnel_remote_port=None,
        )
        store.set_worker_provision_status("ecs-worker-upd", provision_status="ready")
        store.register_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
        )
    else:
        store.register_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={},
        )


def test_update_worker_not_found(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9881)
    status, body = post_update(9881, "missing")
    assert status == 404
    assert body == {"error": "worker not found"}
    server.shutdown()


def test_update_worker_no_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh=False)
    server = start_test_server(store, sample_ssh_config, 9882)
    status, body = post_update(9882, "ecs-worker-upd")
    assert status == 400
    assert body == {"error": "ssh_host_alias required"}
    server.shutdown()


def test_update_worker_active_batches(store, sample_ssh_config):
    _seed_worker(store)
    template = store.create_task_template(
        owner="default",
        name="upd-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-upd",
        batch_options={},
    )
    server = start_test_server(store, sample_ssh_config, 9883)
    status, body = post_update(9883, "ecs-worker-upd")
    assert status == 409
    assert body["error"] == "worker has active batches"
    server.shutdown()


def test_update_worker_starts_job(store, sample_ssh_config):
    _seed_worker(store)
    server = start_test_server(store, sample_ssh_config, 9884)
    with patch.object(WorkerUpdater, "start_job_async") as mock_start:
        status, body = post_update(9884, "ecs-worker-upd", {"targets": ["aeo"]})
    assert status == 202
    assert body["workerId"] == "ecs-worker-upd"
    assert body["targets"] == ["aeo"]
    assert body["jobId"].startswith("upd-")
    mock_start.assert_called_once()
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_update_worker_api.py -v`
Expected: FAIL — 404 on update route or `Handler.worker_updater` missing

- [ ] **Step 3: Write minimal implementation**

In `Handler` class, add class attribute:

```python
    worker_updater: WorkerUpdater | None = None
```

Add import at top of `server.py`:

```python
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater
```

In `do_GET`, before `/api/workers/provision/` block (~line 726), add:

```python
        if path.startswith("/api/workers/update/"):
            job_id = path.split("/")[4]
            job = self.store.get_worker_update_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "jobId": job["job_id"],
                    "workerId": job["worker_id"],
                    "status": job["status"],
                    "targets": job["targets"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
```

In `do_POST`, before `/settings` handler (~line 1209), add update create + cancel routes:

```python
        if path.startswith("/api/workers/update/") and path.endswith("/cancel"):
            job_id = path.split("/")[4]
            job = self.store.get_worker_update_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            if self.worker_updater is None:
                _json_response(self, {"error": "worker updater unavailable"}, 500)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == job["worker_id"]),
                None,
            )
            ssh_alias = str(worker.get("ssh_host_alias") or "") if worker else ""
            connection_mode = str(worker.get("connection_mode") or "tunnel") if worker else "tunnel"
            self.worker_updater.cancel_job(
                job_id,
                worker_id=str(job["worker_id"]),
                ssh_host_alias=ssh_alias,
                connection_mode=connection_mode,
            )
            _json_response(self, {"ok": True, "jobId": job_id, "status": "cancelled"})
            return
        if path.startswith("/api/workers/") and path.endswith("/update"):
            worker_id = path.split("/")[3]
            if self.worker_updater is None:
                _json_response(self, {"error": "worker updater unavailable"}, 500)
                return
            worker = next(
                (item for item in self.store.list_workers() if item["worker_id"] == worker_id),
                None,
            )
            if not worker:
                _json_response(self, {"error": "worker not found"}, 404)
                return
            ssh_alias = str(worker.get("ssh_host_alias") or "").strip()
            if not ssh_alias:
                _json_response(self, {"error": "ssh_host_alias required"}, 400)
                return
            counts = self.store.worker_has_active_batches(worker_id)
            if counts["runningCount"] > 0 or counts["queuedCount"] > 0:
                _json_response(
                    self,
                    {
                        "error": "worker has active batches",
                        "runningCount": counts["runningCount"],
                        "queuedCount": counts["queuedCount"],
                    },
                    409,
                )
                return
            if self.store.get_active_worker_update_job_for_worker(worker_id):
                _json_response(self, {"error": "update already in progress"}, 409)
                return
            latest_prov = self.store.get_latest_provision_job_for_worker(worker_id)
            if latest_prov and str(latest_prov["status"]) in {"pending", "running"}:
                _json_response(self, {"error": "provision in progress"}, 409)
                return
            raw_targets = body.get("targets")
            if raw_targets is None:
                targets = ["aeo", "harbor"]
            elif isinstance(raw_targets, list):
                targets = [str(item) for item in raw_targets]
            else:
                _json_response(self, {"error": "targets must be an array"}, 400)
                return
            allowed = {"aeo", "harbor"}
            if not targets or any(item not in allowed for item in targets):
                _json_response(self, {"error": "targets must contain aeo and/or harbor"}, 400)
                return
            job_id = new_id("upd")
            steps = self.worker_updater.initial_steps(targets)
            self.store.create_worker_update_job(
                job_id=job_id,
                worker_id=worker_id,
                targets=targets,
                steps=steps,
            )
            self.worker_updater.start_job_async(
                job_id=job_id,
                worker_id=worker_id,
                targets=targets,
                ssh_host_alias=ssh_alias,
                connection_mode=str(worker.get("connection_mode") or "direct"),
                controller_internal_ip=worker.get("controller_internal_ip"),
                tunnel_remote_port=worker.get("tunnel_remote_port"),
                display_name=str(worker.get("display_name") or worker_id),
                slots_total=int(worker.get("slots_total") or 1),
                worker=worker,
            )
            _json_response(
                self,
                {
                    "jobId": job_id,
                    "workerId": worker_id,
                    "status": "pending",
                    "targets": targets,
                },
                202,
            )
            return
```

In `main()`, after `provisioner = Provisioner(...)`, add:

```python
    worker_updater = WorkerUpdater(
        store=store,
        ssh_config_path=ssh_config_path,
        auth_token=str(args.auth_token or "") or "",
        controller_port=args.port,
        provisioner=provisioner,
    )
```

And wire: `Handler.worker_updater = worker_updater`

Update `do_DELETE` reserved paths (~line 1301) to include `"update"`:

```python
            reserved = {"provision", "runtime", "register", "claim", "heartbeat", "job-archive", "update"}
```

In `do_DELETE`, before decommission, cancel active update job (mirror provision cancel):

```python
            if self.worker_updater is not None:
                active_update = self.store.get_active_worker_update_job_for_worker(worker_id)
                if active_update:
                    self.worker_updater.cancel_job(
                        str(active_update["job_id"]),
                        worker_id=worker_id,
                        ssh_host_alias=str(worker.get("ssh_host_alias") or ""),
                        connection_mode=str(worker.get("connection_mode") or "tunnel"),
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_update_worker_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_update_worker_api.py
git commit -m "feat: add worker update API endpoints"
```

---

### Task 6: Dashboard UI — update button and progress modal

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py` (~1377-1430 worker detail, ~1525-1665 provision modal pattern)

- [ ] **Step 1: Add state fields**

Near existing `provisionJob` state (~line 680), add:

```javascript
      updateJob: null,
      updateWorkerPhase: "confirm",
```

- [ ] **Step 2: Add update button to worker detail**

In `renderWorkerDetail`, inside the actions `<div>` before delete button, add update button HTML:

```javascript
      const hasSsh = Boolean(worker.ssh_host_alias);
      const hasActiveBatches = (runtime.runningCount || 0) > 0 || (runtime.queuedCount || 0) > 0;
      const isUpdating = worker.update_status === "updating";
      let updateBtnLabel = "更新代码";
      let updateBtnDisabled = "";
      let updateBtnTitle = "";
      if (!hasSsh) {
        updateBtnDisabled = " disabled";
        updateBtnTitle = ' title="需要 SSH 配置才能远程更新"';
      } else if (hasActiveBatches) {
        updateBtnDisabled = " disabled";
        updateBtnTitle = ' title="请先等待运行中的 batch 完成"';
      } else if (isUpdating) {
        updateBtnLabel = "更新中…";
      }
```

Insert button in actions:

```javascript
            '<button class="ghost" type="button" id="updateWorkerBtn"' + updateBtnDisabled + updateBtnTitle + '>' + updateBtnLabel + '</button>' +
```

After form listeners, wire click handler:

```javascript
      const updateBtn = root.querySelector("#updateWorkerBtn");
      if (updateBtn) {
        updateBtn.addEventListener("click", () => {
          if (isUpdating && worker.last_update_job_id) {
            state.updateJob = { jobId: worker.last_update_job_id, workerId: worker.worker_id };
            state.updateWorkerPhase = "progress";
            openUpdateWorkerModal();
            return;
          }
          state.updateJob = { workerId: worker.worker_id };
          state.updateWorkerPhase = "confirm";
          openUpdateWorkerModal();
        });
      }
```

- [ ] **Step 3: Add update modal HTML shell**

Near `addWorkerModal` in INDEX_HTML, add:

```html
    <div id="updateWorkerModal" class="modal hidden">
      <div class="modal-card">
        <div class="modal-header">
          <h2>更新 Worker 代码</h2>
          <button class="ghost" type="button" id="updateWorkerModalClose">×</button>
        </div>
        <div id="updateWorkerModalBody"></div>
      </div>
    </div>
```

- [ ] **Step 4: Add modal render/poll functions**

Add functions mirroring provision pattern:

```javascript
    let updatePollTimer = null;

    function renderUpdateConfirmForm() {
      return '' +
        '<p class="subtle" style="margin-bottom:12px">更新将停止 Worker Daemon 并重启，期间该 worker 无法领取新任务。</p>' +
        '<form id="updateWorkerForm">' +
          '<label style="display:block;margin-bottom:8px"><input type="checkbox" name="targetAeo" checked /> 更新 AEO (agent-eval-orchestrator)</label>' +
          '<label style="display:block;margin-bottom:16px"><input type="checkbox" name="targetHarbor" checked /> 更新 Harbor</label>' +
          '<div class="actions">' +
            '<button class="primary" type="submit">开始更新</button>' +
            '<button class="ghost" type="button" id="updateWorkerCancelForm">取消</button>' +
          '</div>' +
        '</form>';
    }

    function renderUpdateProgress() {
      const job = state.updateJob?.detail;
      if (!job) {
        return '<div class="empty">正在加载更新状态...</div>';
      }
      const stepsHtml = (job.steps || []).map(step =>
        '<div class="queue-row">' +
          '<div class="queue-title"><strong>' + esc(step.label) + '</strong>' + badge(step.status) + '</div>' +
        '</div>'
      ).join("");
      const actions = [];
      if (job.status === "running" || job.status === "pending") {
        actions.push('<button class="ghost" type="button" id="updateCancelBtn">取消</button>');
      }
      if (job.status === "succeeded") {
        actions.push('<button class="primary" type="button" id="updateCloseBtn">关闭</button>');
      }
      if (job.status === "failed" || job.status === "cancelled") {
        actions.push('<button class="primary" type="button" id="updateRetryBtn">重试</button>');
        actions.push('<button class="ghost" type="button" id="updateCloseBtn">关闭</button>');
      }
      return '' +
        '<div class="detail-grid" style="margin-bottom:16px">' +
          '<div class="stat"><div class="subtle">Job</div><strong><code>' + esc(job.jobId) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Worker</div><strong><code>' + esc(job.workerId) + '</code></strong></div>' +
          '<div class="stat"><div class="subtle">Status</div><strong>' + badge(job.status) + '</strong></div>' +
        '</div>' +
        stepsHtml +
        (job.errorText ? '<div class="empty" style="color:var(--bad);margin-top:12px">' + esc(job.errorText) + '</div>' : '') +
        '<pre style="margin-top:12px;max-height:240px">' + esc(job.logTail || "") + '</pre>' +
        '<div class="actions" style="margin-top:12px">' + actions.join("") + '</div>';
    }

    function closeUpdateWorkerModal() {
      state.updateJob = null;
      state.updateWorkerPhase = "confirm";
      if (updatePollTimer) {
        clearInterval(updatePollTimer);
        updatePollTimer = null;
      }
      document.getElementById("updateWorkerModal").classList.add("hidden");
    }

    async function renderUpdateWorkerModal() {
      const body = document.getElementById("updateWorkerModalBody");
      if (state.updateWorkerPhase === "confirm") {
        body.innerHTML = renderUpdateConfirmForm();
        document.getElementById("updateWorkerCancelForm").addEventListener("click", closeUpdateWorkerModal);
        document.getElementById("updateWorkerForm").addEventListener("submit", submitUpdateWorkerForm);
        return;
      }
      body.innerHTML = renderUpdateProgress();
      bindUpdateProgressActions();
    }

    function openUpdateWorkerModal() {
      renderUpdateWorkerModal();
      document.getElementById("updateWorkerModal").classList.remove("hidden");
      if (state.updateWorkerPhase === "progress" && state.updateJob?.jobId) {
        startUpdatePolling();
      }
    }

    async function submitUpdateWorkerForm(event) {
      event.preventDefault();
      const form = new FormData(event.target);
      const targets = [];
      if (form.get("targetAeo")) targets.push("aeo");
      if (form.get("targetHarbor")) targets.push("harbor");
      if (!targets.length) {
        alert("请至少选择一个仓库");
        return;
      }
      const workerId = state.updateJob.workerId;
      const result = await api("/api/workers/" + encodeURIComponent(workerId) + "/update", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ targets }),
      });
      state.updateJob = { jobId: result.jobId, workerId: result.workerId };
      state.updateWorkerPhase = "progress";
      await renderUpdateWorkerModal();
      startUpdatePolling();
    }

    async function pollUpdateJob() {
      if (!state.updateJob?.jobId) return;
      const detail = await api("/api/workers/update/" + encodeURIComponent(state.updateJob.jobId));
      state.updateJob.detail = detail;
      await renderUpdateWorkerModal();
      if (["succeeded", "failed", "cancelled"].includes(detail.status)) {
        clearInterval(updatePollTimer);
        updatePollTimer = null;
        await loadDashboard();
      }
    }

    function startUpdatePolling() {
      if (updatePollTimer) clearInterval(updatePollTimer);
      pollUpdateJob();
      updatePollTimer = setInterval(pollUpdateJob, 2000);
    }

    function bindUpdateProgressActions() {
      const cancelBtn = document.getElementById("updateCancelBtn");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", async () => {
          await api("/api/workers/update/" + encodeURIComponent(state.updateJob.jobId) + "/cancel", {
            method: "POST",
          });
          await pollUpdateJob();
        });
      }
      const closeBtn = document.getElementById("updateCloseBtn");
      if (closeBtn) closeBtn.addEventListener("click", closeUpdateWorkerModal);
      const retryBtn = document.getElementById("updateRetryBtn");
      if (retryBtn) {
        retryBtn.addEventListener("click", () => {
          state.updateWorkerPhase = "confirm";
          renderUpdateWorkerModal();
        });
      }
    }
```

Wire modal close button in page init (same pattern as addWorkerModal):

```javascript
    document.getElementById("updateWorkerModalClose").addEventListener("click", closeUpdateWorkerModal);
```

- [ ] **Step 5: Manual smoke test**

Run controller locally, open Workers tab, select a worker with SSH alias, click **更新代码**, confirm both checkboxes, verify modal shows steps and polls.

Run: `uv run --extra dev pytest tests/ -v --ignore=tests/e2e 2>/dev/null | tail -5`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add worker update button and progress modal to dashboard"
```

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| Manual trigger (API + UI) | Task 5, Task 6 |
| Configurable targets (default both) | Task 3, Task 5, Task 6 |
| Pull current branch | Task 4 (`git pull` only) |
| Block on active batches | Task 5 |
| Require SSH | Task 5, Task 6 |
| Async job with steps | Task 2, Task 3, Task 4 |
| `worker_update_jobs` table | Task 2 |
| Dynamic path resolution | Task 1, Task 3 |
| Reuse decommission/tunnel/wait_register | Task 4 |
| Cancel job API | Task 5 |
| Delete cancels active update | Task 5 |
| `last_update_job_id` / `update_status` | Task 2 |
| Dashboard button states | Task 6 |
| Error: git pull / uv sync failure | Task 4 |
| Redact sensitive logs | Task 4 (`redact_sensitive_log`) |

## Self-Review Notes

- All tasks include complete test code and implementation snippets — no TBD placeholders.
- Method names consistent: `create_worker_update_job`, `get_active_worker_update_job_for_worker`, `WorkerUpdater.resolve_paths`.
- Tunnel restart handled inside `restart_daemon` step without adding a separate UI step (matches spec step list).
- Existing provision flow unchanged — `build_daemon_start_command()` defaults preserve backward compatibility.
