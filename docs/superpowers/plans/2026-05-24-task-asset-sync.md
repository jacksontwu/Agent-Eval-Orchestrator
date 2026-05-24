# Task Asset Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When creating a Harbor eval task, sync per-worker dataset shards and bitfun-cli from the Controller to each worker asynchronously; batches stay unclaimable until sync completes, then auto-clean synced assets when the run finishes.

**Architecture:** A run-level `AssetSyncJob` (mirroring `Provisioner`) runs in background threads — one thread per worker, sequential `sync_cases` then `sync_bitfun` steps. Shared SSH/rsync/scp helpers live in `ssh_runner.py` (extracted from `provisioner.py`). SQLite stores job state in `asset_sync_jobs`; runs get `sync_status` / `sync_manifest_json`. Batches start as `pending_sync`, become `queued` per worker on success.

**Tech Stack:** Python 3.10+, stdlib (`http.server`, `sqlite3`, `subprocess`, `threading`, `shutil`), OpenSSH (`ssh`, `scp`, `rsync`), embedded HTML/JS dashboard, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/controller/ssh_runner.py` | Shared `ssh`/`scp`/`rsync` subprocess helpers |
| `src/agent_eval_orchestrator/controller/asset_syncer.py` | Manifest builders, local/remote sync, `AssetSyncer` job runner, cleanup |
| `src/agent_eval_orchestrator/controller/provisioner.py` | Refactored to use `ssh_runner` |
| `src/agent_eval_orchestrator/controller/server.py` | Validation, create-and-distribute changes, sync GET endpoints, wire `AssetSyncer` |
| `src/agent_eval_orchestrator/controller/static.py` | Create form fields, sync progress polling, run sync badge |
| `src/agent_eval_orchestrator/storage/store.py` | Schema migrations, asset sync CRUD, batch status transitions, executor config update |
| `tests/storage/test_asset_sync_store.py` | Schema + CRUD tests |
| `tests/controller/test_ssh_runner.py` | SSH helper unit tests |
| `tests/controller/test_asset_syncer.py` | Sync logic + mocked subprocess tests |
| `tests/controller/test_create_task_sync_api.py` | HTTP handler tests for create + sync endpoints |

---

### Task 1: Asset sync schema & store CRUD

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`_ensure_schema_migrations`, new CRUD methods, `_run_item`)
- Create: `tests/storage/test_asset_sync_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_asset_sync_store.py`:

```python
import json

from agent_eval_orchestrator.core.ids import new_id


def test_asset_sync_schema_and_crud(store):
    template = store.create_task_template(
        owner="default",
        name="sync-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
    )
    run = store.create_run(template_id=template["template_id"], display_name="sync-run")
    job_id = new_id("sync")

    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="pending",
        sync_job_id=job_id,
        sync_manifest={"datasetPath": "/tmp/dataset", "workers": {}},
    )
    updated_run = store.get_run(run["run_id"])
    assert updated_run["sync_status"] == "pending"
    assert updated_run["sync_job_id"] == job_id
    assert updated_run["sync_manifest"]["datasetPath"] == "/tmp/dataset"

    job = store.create_asset_sync_job(
        job_id=job_id,
        run_id=run["run_id"],
        steps=[
            {
                "workerId": "local-a",
                "steps": [
                    {"id": "sync_cases", "label": "同步 dataset case", "status": "pending"},
                    {"id": "sync_bitfun", "label": "同步 bitfun-cli", "status": "pending"},
                ],
            }
        ],
    )
    assert job["status"] == "pending"
    assert job["steps"][0]["workerId"] == "local-a"

    store.append_asset_sync_log(job_id, "line one\n")
    updated = store.update_asset_sync_job(
        job_id,
        status="running",
        current_step="sync_cases",
        steps=[
            {
                "workerId": "local-a",
                "steps": [
                    {"id": "sync_cases", "label": "同步 dataset case", "status": "running"},
                    {"id": "sync_bitfun", "label": "同步 bitfun-cli", "status": "pending"},
                ],
            }
        ],
    )
    assert updated["log_text"].endswith("line one\n")
    assert updated["status"] == "running"

    fetched = store.get_asset_sync_job(job_id)
    assert fetched is not None
    assert fetched["run_id"] == run["run_id"]
    assert fetched["log_tail"].endswith("line one\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_asset_sync_schema_and_crud -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'create_asset_sync_job'`

- [ ] **Step 3: Write minimal implementation**

In `store.py` `_ensure_schema_migrations`, after provision_jobs block, add:

```python
            run_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            for column, ddl in {
                "sync_status": "TEXT NOT NULL DEFAULT ''",
                "sync_job_id": "TEXT",
                "sync_manifest_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in run_columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS asset_sync_jobs (
                    job_id       TEXT PRIMARY KEY,
                    run_id       TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    current_step TEXT,
                    steps_json   TEXT NOT NULL,
                    log_text     TEXT NOT NULL DEFAULT '',
                    error_text   TEXT,
                    created_at   TEXT NOT NULL,
                    finished_at  TEXT
                );
                """
            )
```

Add methods to `Store`:

```python
    def update_run_sync_fields(
        self,
        *,
        run_id: str,
        sync_status: str | None = None,
        sync_job_id: str | None = None,
        sync_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        next_status = sync_status if sync_status is not None else str(run.get("sync_status") or "")
        next_job_id = sync_job_id if sync_job_id is not None else run.get("sync_job_id")
        next_manifest = (
            json.dumps(sync_manifest, ensure_ascii=False)
            if sync_manifest is not None
            else json.dumps(run.get("sync_manifest") or {}, ensure_ascii=False)
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET sync_status = ?, sync_job_id = ?, sync_manifest_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (next_status, next_job_id, next_manifest, now_iso(), run_id),
            )
        return self.get_run(run_id)

    def create_asset_sync_job(
        self,
        *,
        job_id: str,
        run_id: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO asset_sync_jobs(
                    job_id, run_id, status, current_step,
                    steps_json, log_text, error_text, created_at, finished_at
                ) VALUES(?, ?, 'pending', NULL, ?, '', NULL, ?, NULL)
                """,
                (job_id, run_id, json.dumps(steps, ensure_ascii=False), now),
            )
            row = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(row)

    def get_asset_sync_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(row) if row else None

    def get_asset_sync_job_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM asset_sync_jobs
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._asset_sync_job_item(row) if row else None

    def append_asset_sync_log(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE asset_sync_jobs SET log_text = log_text || ? WHERE job_id = ?",
                (chunk, job_id),
            )

    def update_asset_sync_job(
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
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
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
                UPDATE asset_sync_jobs
                SET status = ?, current_step = ?, steps_json = ?,
                    error_text = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (next_status, next_step, next_steps_json, next_error, finished_at, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM asset_sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._asset_sync_job_item(updated)

    def _asset_sync_job_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["steps"] = json.loads(item.pop("steps_json"))
        item["log_tail"] = item["log_text"][-8192:] if item.get("log_text") else ""
        return item
```

Update `_run_item`:

```python
    def _run_item(self, row: sqlite3.Row | None) -> dict[str, Any]:
        item = dict(row)
        manifest_raw = item.pop("sync_manifest_json", "{}")
        item["sync_manifest"] = json.loads(manifest_raw or "{}")
        if not item.get("sync_status"):
            item["sync_status"] = ""
        return item
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_asset_sync_schema_and_crud -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_asset_sync_store.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: add asset sync job schema and store CRUD"
```

---

### Task 2: Batch `pending_sync` status & executor config update

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`create_batch`, batch promotion helpers, `update_task_template_executor_config`)
- Modify: `tests/storage/test_asset_sync_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_asset_sync_store.py`:

```python
def test_pending_sync_batches_and_promotion(store):
    template = store.create_task_template(
        owner="default",
        name="batch-sync",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
    )
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
    )
    assert batch["status"] == "pending_sync"

    claimed = store.claim_next_batch("worker-a")
    assert claimed is None

    store.promote_worker_batches_to_queued(run_id=run["run_id"], worker_id="worker-a")
    claimed = store.claim_next_batch("worker-a")
    assert claimed is not None
    assert claimed["batch"]["batch_id"] == batch["batch_id"]


def test_update_task_template_executor_config(store):
    template = store.create_task_template(
        owner="default",
        name="cfg",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True, "datasetPathByWorker": {}},
    )
    updated = store.update_task_template_executor_config(
        template["template_id"],
        {
            "datasetPathByWorker": {"worker-a": "/sync/run-1/dataset"},
            "mountsByWorker": {
                "worker-a": [
                    {"type": "bind", "source": "/sync/run-1/bitfun/bitfun-cli", "target": "/usr/local/bin/bitfun-cli"},
                ]
            },
        },
    )
    assert updated["executor_config"]["datasetPathByWorker"]["worker-a"] == "/sync/run-1/dataset"
    assert updated["executor_config"]["mountsByWorker"]["worker-a"][0]["source"].endswith("bitfun-cli")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_pending_sync_batches_and_promotion -v`
Expected: FAIL — `create_batch() got an unexpected keyword argument 'initial_status'`

- [ ] **Step 3: Write minimal implementation**

Change `create_batch` signature and INSERT:

```python
    def create_batch(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        preferred_worker_id: str | None,
        batch_options: dict[str, Any] | None,
        initial_status: str = "queued",
    ) -> dict[str, Any]:
        ...
                ) VALUES(?, ?, ?, ?, NULL, ?, NULL, ?, '{}', ?, ?, '{}', '{}', ?, ?, NULL, NULL, NULL)
                """,
                (
                    batch_id,
                    run_id,
                    run["owner"],
                    initial_status,
                    preferred_worker_id,
                    ...
```

Add:

```python
    def create_sharded_batches(
        self,
        *,
        run_id: str,
        selected_case_ids: list[str],
        worker_ids: list[str],
        batch_options: dict[str, Any] | None,
        initial_status: str = "queued",
    ) -> list[dict[str, Any]]:
        ...
            created.append(
                self.create_batch(
                    ...
                    initial_status=initial_status,
                )
            )
```

Add promotion helpers:

```python
    def promote_worker_batches_to_queued(self, *, run_id: str, worker_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE batches
                SET status = 'queued'
                WHERE run_id = ? AND preferred_worker_id = ? AND status = 'pending_sync'
                """,
                (run_id, worker_id),
            )
        return int(cursor.rowcount)

    def mark_worker_batches_sync_failed(self, *, run_id: str, worker_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE batches
                SET status = 'sync_failed'
                WHERE run_id = ? AND preferred_worker_id = ? AND status = 'pending_sync'
                """,
                (run_id, worker_id),
            )
        return int(cursor.rowcount)

    def update_task_template_executor_config(
        self,
        template_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        template = self.get_task_template(template_id)
        if not template:
            raise RuntimeError("template not found")
        config = dict(template["executor_config"])
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                merged = dict(config[key])
                merged.update(value)
                config[key] = merged
            else:
                config[key] = value
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_templates
                SET executor_config_json = ?, updated_at = ?
                WHERE template_id = ?
                """,
                (json.dumps(config, ensure_ascii=False), now, template_id),
            )
        updated = self.get_task_template(template_id)
        if not updated:
            raise RuntimeError("template not found after update")
        return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_asset_sync_store.py src/agent_eval_orchestrator/storage/store.py
git commit -m "feat: support pending_sync batches and executor config updates"
```

---

### Task 3: Extract shared SSH runner from provisioner

**Files:**
- Create: `src/agent_eval_orchestrator/controller/ssh_runner.py`
- Modify: `src/agent_eval_orchestrator/controller/provisioner.py`
- Create: `tests/controller/test_ssh_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_ssh_runner.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_eval_orchestrator.controller.ssh_runner import SshRunner


def test_ssh_run_builds_command(sample_ssh_config):
    runner = SshRunner(sample_ssh_config)
    with patch("agent_eval_orchestrator.controller.ssh_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        result = runner.ssh_run("aeo-ecs-0004", "echo ok", check=True)
        assert result.returncode == 0
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "ssh"
        assert "-F" in cmd
        assert str(sample_ssh_config) in cmd
        assert "aeo-ecs-0004" in cmd
        assert cmd[-1] == "echo ok"


def test_rsync_dir_builds_command(sample_ssh_config, tmp_path):
    runner = SshRunner(sample_ssh_config)
    src = tmp_path / "src"
    src.mkdir()
    with patch("agent_eval_orchestrator.controller.ssh_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.rsync_dir(src, "aeo-ecs-0004:/tmp/target/", remote=True)
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "rsync"
        assert "-az" in cmd
        assert "ssh -F" in " ".join(cmd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_ssh_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.ssh_runner'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/ssh_runner.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable


class SshRunner:
    def __init__(self, ssh_config_path: Path, *, log_fn: Callable[[str], None] | None = None) -> None:
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self._log_fn = log_fn

    def _log(self, chunk: str) -> None:
        if self._log_fn and chunk:
            self._log_fn(chunk)

    def ssh_base(self) -> list[str]:
        return ["ssh", "-F", str(self.ssh_config_path), "-o", "BatchMode=yes"]

    def ssh_run(
        self,
        host_alias: str,
        remote_command: str,
        *,
        check: bool = True,
        connect_timeout_sec: int | None = None,
        detach: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [*self.ssh_base()]
        if detach:
            cmd.append("-n")
        if connect_timeout_sec is not None:
            cmd.extend(["-o", f"ConnectTimeout={connect_timeout_sec}"])
        cmd.extend([host_alias, remote_command])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
        return result

    def scp_file(self, local_path: Path, remote_target: str) -> None:
        cmd = [
            "scp",
            "-F",
            str(self.ssh_config_path),
            "-o",
            "BatchMode=yes",
            str(local_path),
            remote_target,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "scp failed")

    def rsync_dir(
        self,
        source: Path,
        destination: str,
        *,
        remote: bool,
    ) -> None:
        source_arg = f"{source}/" if source.is_dir() else str(source)
        cmd = ["rsync", "-az"]
        if remote:
            cmd.extend(["-e", f"ssh -F {self.ssh_config_path}"])
        cmd.extend([source_arg, destination])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "rsync failed")

    def remote_mkdir_p(self, host_alias: str, remote_path: str) -> None:
        self.ssh_run(host_alias, f"mkdir -p {remote_path}")

    def remote_rm_rf(self, host_alias: str, remote_path: str) -> None:
        self.ssh_run(host_alias, f"rm -rf {remote_path}", check=False)
```

Refactor `provisioner.py`:
- Add `self.ssh = SshRunner(self.ssh_config_path, log_fn=self._log)` in `__init__`
- Replace `_ssh_base`, `_ssh_run` calls with `self.ssh.ssh_run(...)`
- Replace inline `scp` in `_bootstrap` with `self.ssh.scp_file(...)`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/controller/test_ssh_runner.py tests/controller/test_provisioner_runner.py tests/controller/test_provision_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/ssh_runner.py src/agent_eval_orchestrator/controller/provisioner.py tests/controller/test_ssh_runner.py
git commit -m "refactor: extract shared SSH runner from provisioner"
```

---

### Task 4: Asset sync helpers (local detection, manifest, validation)

**Files:**
- Create: `src/agent_eval_orchestrator/controller/asset_syncer.py` (helpers only)
- Create: `tests/controller/test_asset_syncer.py` (helper tests)

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_asset_syncer.py`:

```python
import os
from pathlib import Path

import pytest

from agent_eval_orchestrator.controller.asset_syncer import (
    build_sync_manifest,
    initial_worker_steps,
    is_local_worker,
    set_worker_step_status,
    validate_create_task_assets,
    worker_executor_paths,
)


def test_is_local_worker_by_flag():
    worker = {"capabilities": {"localToController": True}}
    assert is_local_worker(worker, Path("/tmp/controller")) is True


def test_is_local_worker_by_existing_shared_root(tmp_path):
    shared = tmp_path / "runtime"
    shared.mkdir()
    worker = {"capabilities": {"sharedRoot": str(shared)}}
    assert is_local_worker(worker, tmp_path) is True


def test_validate_create_task_assets(tmp_path, store):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()

    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        capabilities={"sharedRoot": str(tmp_path / "runtime")},
    )
    validate_create_task_assets(
        dataset_path=dataset,
        bitfun_cli_path=bitfun_cli,
        bitfun_config_dir=config_dir,
        case_ids=["case-a"],
        workers=store.list_workers(),
        worker_ids=["local-a"],
        controller_shared_root=tmp_path,
    )


def test_validate_rejects_remote_without_ssh(tmp_path, store):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()

    store.register_worker(
        worker_id="remote-a",
        display_name="remote",
        host="remote",
        slots_total=1,
        capabilities={"sharedRoot": "/nonexistent/on/controller/runtime"},
    )
    with pytest.raises(RuntimeError, match="ssh_host_alias"):
        validate_create_task_assets(
            dataset_path=dataset,
            bitfun_cli_path=bitfun_cli,
            bitfun_config_dir=config_dir,
            case_ids=["case-a"],
            workers=store.list_workers(),
            worker_ids=["remote-a"],
            controller_shared_root=tmp_path,
        )


def test_build_sync_manifest(tmp_path):
    manifest = build_sync_manifest(
        run_id="run-abc",
        dataset_path=Path("/ctrl/dataset"),
        bitfun_cli_path=Path("/ctrl/bitfun-cli"),
        bitfun_config_dir=Path("/ctrl/.config/bitfun"),
        worker_shards={"remote-a": ["case-1"], "local-a": ["case-2"]},
        workers_by_id={
            "remote-a": {"worker_id": "remote-a", "ssh_host_alias": "aeo-ecs-0004", "capabilities": {"sharedRoot": "/home/djn/worker/runtime"}},
            "local-a": {"worker_id": "local-a", "capabilities": {"sharedRoot": str(tmp_path / "runtime")}},
        },
        controller_shared_root=tmp_path,
    )
    assert manifest["workers"]["remote-a"]["transport"] == "ssh"
    assert manifest["workers"]["remote-a"]["caseIds"] == ["case-1"]
    assert manifest["workers"]["local-a"]["transport"] == "local"
    assert manifest["workers"]["remote-a"]["targetRoot"].endswith("/sync/run-abc")


def test_worker_executor_paths():
    paths = worker_executor_paths("/tmp/sync/run-1")
    assert paths["datasetPath"] == "/tmp/sync/run-1/dataset"
    assert paths["mounts"][0]["target"] == "/usr/local/bin/bitfun-cli"
    assert paths["agentEnv"]["XDG_CONFIG_HOME"] == "/testbed/.config"


def test_initial_worker_steps_and_status():
    steps = initial_worker_steps(["worker-a", "worker-b"])
    assert len(steps) == 2
    updated = set_worker_step_status(steps, "worker-a", "sync_cases", "running")
    worker_a = next(item for item in updated if item["workerId"] == "worker-a")
    assert worker_a["steps"][0]["status"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_eval_orchestrator/controller/asset_syncer.py` with:

```python
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from agent_eval_orchestrator.controller.ssh_runner import SshRunner

if TYPE_CHECKING:
    from agent_eval_orchestrator.storage.store import Store

SYNC_STEP_LABELS = {
    "sync_cases": "同步 dataset case",
    "sync_bitfun": "同步 bitfun-cli",
}


def is_local_worker(worker: dict[str, Any], controller_shared_root: Path) -> bool:
    caps = worker.get("capabilities") or {}
    if caps.get("localToController") is True:
        return True
    shared_root = str(caps.get("sharedRoot") or "").strip()
    if not shared_root:
        return False
    return Path(shared_root).expanduser().exists()


def initial_worker_steps(worker_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "workerId": worker_id,
            "steps": [
                {"id": "sync_cases", "label": SYNC_STEP_LABELS["sync_cases"], "status": "pending"},
                {"id": "sync_bitfun", "label": SYNC_STEP_LABELS["sync_bitfun"], "status": "pending"},
            ],
        }
        for worker_id in worker_ids
    ]


def set_worker_step_status(
    steps: list[dict[str, Any]],
    worker_id: str,
    step_id: str,
    status: str,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for worker_entry in steps:
        entry = dict(worker_entry)
        if entry["workerId"] != worker_id:
            updated.append(entry)
            continue
        next_steps = []
        for step in entry["steps"]:
            item = dict(step)
            if item["id"] == step_id:
                item["status"] = status
            next_steps.append(item)
        entry["steps"] = next_steps
        updated.append(entry)
    return updated


def validate_create_task_assets(
    *,
    dataset_path: Path,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    case_ids: list[str],
    workers: list[dict[str, Any]],
    worker_ids: list[str],
    controller_shared_root: Path,
) -> None:
    if not dataset_path.exists() or not dataset_path.is_dir():
        raise RuntimeError(f"datasetPath not found: {dataset_path}")
    if not bitfun_cli_path.exists() or not os.access(bitfun_cli_path, os.X_OK):
        raise RuntimeError(f"bitfunCliPath must exist and be executable: {bitfun_cli_path}")
    if not bitfun_config_dir.exists() or not bitfun_config_dir.is_dir():
        raise RuntimeError(f"bitfunConfigDir must be an existing directory: {bitfun_config_dir}")
    if not worker_ids:
        raise RuntimeError("workerIds must not be empty")
    workers_by_id = {str(item["worker_id"]): item for item in workers}
    for case_id in case_ids:
        case_dir = dataset_path / case_id
        if not case_dir.is_dir():
            raise RuntimeError(f"case directory not found: {case_id}")
    for worker_id in worker_ids:
        worker = workers_by_id.get(worker_id)
        if not worker:
            raise RuntimeError(f"worker not found: {worker_id}")
        if is_local_worker(worker, controller_shared_root):
            continue
        if not str(worker.get("ssh_host_alias") or "").strip():
            raise RuntimeError(f"worker {worker_id} requires ssh_host_alias for remote asset sync")


def _worker_target_root(worker: dict[str, Any], run_id: str) -> str:
    shared_root = str((worker.get("capabilities") or {}).get("sharedRoot") or "").strip()
    if not shared_root:
        raise RuntimeError(f"worker {worker['worker_id']} missing capabilities.sharedRoot")
    return str(Path(shared_root).expanduser() / "sync" / run_id)


def build_sync_manifest(
    *,
    run_id: str,
    dataset_path: Path,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    worker_shards: dict[str, list[str]],
    workers_by_id: dict[str, dict[str, Any]],
    controller_shared_root: Path,
) -> dict[str, Any]:
    workers: dict[str, Any] = {}
    for worker_id, case_ids in worker_shards.items():
        worker = workers_by_id[worker_id]
        local = is_local_worker(worker, controller_shared_root)
        entry: dict[str, Any] = {
            "caseIds": case_ids,
            "targetRoot": _worker_target_root(worker, run_id),
            "transport": "local" if local else "ssh",
        }
        if not local:
            entry["sshHostAlias"] = str(worker["ssh_host_alias"])
        workers[worker_id] = entry
    return {
        "datasetPath": str(dataset_path),
        "bitfunCliPath": str(bitfun_cli_path),
        "bitfunConfigDir": str(bitfun_config_dir),
        "workers": workers,
    }


def worker_executor_paths(target_root: str) -> dict[str, Any]:
    root = str(Path(target_root))
    return {
        "datasetPath": f"{root}/dataset",
        "mounts": [
            {"type": "bind", "source": f"{root}/bitfun/bitfun-cli", "target": "/usr/local/bin/bitfun-cli"},
            {"type": "bind", "source": f"{root}/bitfun/config", "target": "/testbed/.config/bitfun"},
        ],
        "agentEnv": {"XDG_CONFIG_HOME": "/testbed/.config"},
    }
```

(AssetSyncer class added in Task 5.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_asset_syncer.py
git commit -m "feat: add asset sync validation and manifest helpers"
```

---

### Task 5: Local and remote sync operations

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Modify: `tests/controller/test_asset_syncer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_asset_syncer.py`:

```python
from unittest.mock import MagicMock, patch

from agent_eval_orchestrator.controller.asset_syncer import sync_bitfun_local, sync_cases_local


def test_sync_cases_local(tmp_path):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("x", encoding="utf-8")
    target = tmp_path / "target"
    sync_cases_local(dataset_path=dataset, case_ids=["case-a"], target_dataset_dir=target / "dataset")
    assert (target / "dataset" / "case-a" / "task.toml").read_text(encoding="utf-8") == "x"


def test_sync_bitfun_local_preserves_executable(tmp_path):
    cli = tmp_path / "bitfun-cli"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.toml").write_text("a=1", encoding="utf-8")
    target = tmp_path / "target"
    sync_bitfun_local(
        bitfun_cli_path=cli,
        bitfun_config_dir=config,
        target_bitfun_dir=target / "bitfun",
    )
    copied = target / "bitfun" / "bitfun-cli"
    assert copied.exists()
    assert os.access(copied, os.X_OK)
    assert (target / "bitfun" / "config" / "settings.toml").exists()


def test_sync_cases_remote_uses_rsync(sample_ssh_config, tmp_path):
    from agent_eval_orchestrator.controller.asset_syncer import sync_cases_remote

    dataset = tmp_path / "dataset"
    (dataset / "case-a").mkdir(parents=True)
    runner = MagicMock()
    sync_cases_remote(
        ssh=runner,
        host_alias="aeo-ecs-0004",
        dataset_path=dataset,
        case_ids=["case-a"],
        target_root="/tmp/sync/run-1",
    )
    runner.remote_mkdir_p.assert_called()
    assert runner.rsync_dir.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py::test_sync_cases_local -v`
Expected: FAIL — `ImportError: cannot import name 'sync_cases_local'`

- [ ] **Step 3: Write minimal implementation**

Add to `asset_syncer.py`:

```python
def sync_cases_local(*, dataset_path: Path, case_ids: list[str], target_dataset_dir: Path) -> None:
    target_dataset_dir.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        src = dataset_path / case_id
        dst = target_dataset_dir / case_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def sync_bitfun_local(
    *,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    target_bitfun_dir: Path,
) -> None:
    target_bitfun_dir.mkdir(parents=True, exist_ok=True)
    target_cli = target_bitfun_dir / "bitfun-cli"
    shutil.copy2(bitfun_cli_path, target_cli)
    os.chmod(target_cli, os.stat(bitfun_cli_path).st_mode)
    target_config = target_bitfun_dir / "config"
    if target_config.exists():
        shutil.rmtree(target_config)
    shutil.copytree(bitfun_config_dir, target_config)


def sync_cases_remote(
    *,
    ssh: SshRunner,
    host_alias: str,
    dataset_path: Path,
    case_ids: list[str],
    target_root: str,
) -> None:
    ssh.remote_mkdir_p(host_alias, f"{target_root}/dataset")
    for case_id in case_ids:
        ssh.rsync_dir(
            dataset_path / case_id,
            f"{host_alias}:{target_root}/dataset/{case_id}/",
            remote=True,
        )


def sync_bitfun_remote(
    *,
    ssh: SshRunner,
    host_alias: str,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    target_root: str,
) -> None:
    ssh.remote_mkdir_p(host_alias, f"{target_root}/bitfun")
    ssh.scp_file(bitfun_cli_path, f"{host_alias}:{target_root}/bitfun/bitfun-cli")
    ssh.rsync_dir(
        bitfun_config_dir,
        f"{host_alias}:{target_root}/bitfun/config/",
        remote=True,
    )


def cleanup_sync_target_local(target_root: Path) -> None:
    shutil.rmtree(target_root, ignore_errors=True)


def cleanup_sync_target_remote(*, ssh: SshRunner, host_alias: str, target_root: str) -> None:
    ssh.remote_rm_rf(host_alias, target_root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_asset_syncer.py
git commit -m "feat: add local and remote asset sync operations"
```

---

### Task 6: AssetSyncer job runner

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Modify: `tests/controller/test_asset_syncer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_asset_syncer.py`:

```python
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.core.ids import new_id


def test_asset_syncer_promotes_batches_on_success(store, tmp_path, sample_ssh_config):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()
    shared = tmp_path / "runtime"
    shared.mkdir()

    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        capabilities={"sharedRoot": str(shared), "localToController": True},
    )
    template = store.create_task_template(
        owner="default",
        name="sync-run",
        dataset_ref=str(dataset),
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
    )
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="local-a",
        batch_options={},
        initial_status="pending_sync",
    )
    manifest = {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(config_dir),
        "workers": {
            "local-a": {
                "caseIds": ["case-a"],
                "targetRoot": str(shared / "sync" / run["run_id"]),
                "transport": "local",
            }
        },
    }
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="pending",
        sync_manifest=manifest,
    )
    job_id = new_id("sync")
    store.create_asset_sync_job(job_id=job_id, run_id=run["run_id"], steps=[])

    syncer = AssetSyncer(store=store, ssh_config_path=sample_ssh_config, controller_shared_root=tmp_path)
    syncer.run_job(job_id=job_id, run_id=run["run_id"], template_id=template["template_id"])

    updated_run = store.get_run(run["run_id"])
    assert updated_run["sync_status"] == "succeeded"
    job = store.get_asset_sync_job(job_id)
    assert job["status"] == "succeeded"
    claimed = store.claim_next_batch("local-a")
    assert claimed is not None
    updated_template = store.get_task_template(template["template_id"])
    assert updated_template["executor_config"]["datasetPathByWorker"]["local-a"].endswith("/dataset")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py::test_asset_syncer_promotes_batches_on_success -v`
Expected: FAIL — `ImportError: cannot import name 'AssetSyncer'`

- [ ] **Step 3: Write minimal implementation**

Add `AssetSyncer` class to `asset_syncer.py`:

```python
class AssetSyncer:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        controller_shared_root: Path,
    ) -> None:
        self.store = store
        self.controller_shared_root = controller_shared_root.expanduser().resolve()
        self._current_job_id = ""
        self.ssh = SshRunner(ssh_config_path, log_fn=self._log)

    def start_job_async(self, **kwargs: Any) -> None:
        job_id = str(kwargs["job_id"])
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        thread.start()

    def _log(self, chunk: str) -> None:
        if self._current_job_id:
            self.store.append_asset_sync_log(self._current_job_id, chunk)

    def run_job(self, *, job_id: str, run_id: str, template_id: str) -> None:
        self._current_job_id = job_id
        run = self.store.get_run(run_id)
        if not run:
            raise RuntimeError("run not found")
        manifest = dict(run.get("sync_manifest") or {})
        worker_entries = manifest.get("workers") or {}
        worker_ids = list(worker_entries.keys())
        steps = initial_worker_steps(worker_ids)
        self.store.update_asset_sync_job(job_id, status="running", steps=steps)
        self.store.update_run_sync_fields(run_id=run_id, sync_status="running")

        errors: list[str] = []
        lock = threading.Lock()

        def worker_thread(worker_id: str) -> None:
            nonlocal steps
            entry = worker_entries[worker_id]
            try:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "running")
                    self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_cases", steps=steps)
                self._sync_cases(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "running")
                    self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_bitfun", steps=steps)
                self._sync_bitfun(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                    self.store.update_asset_sync_job(job_id, steps=steps)
                paths = worker_executor_paths(str(entry["targetRoot"]))
                self.store.update_task_template_executor_config(
                    template_id,
                    {
                        "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                        "mountsByWorker": {worker_id: paths["mounts"]},
                        "agentEnvByWorker": {worker_id: paths["agentEnv"]},
                    },
                )
                self.store.promote_worker_batches_to_queued(run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "failed")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "failed")
                    self.store.update_asset_sync_job(job_id, steps=steps)
                self.store.mark_worker_batches_sync_failed(run_id=run_id, worker_id=worker_id)
                errors.append(f"{worker_id}: {exc}")

        threads = [
            threading.Thread(target=worker_thread, args=(worker_id,), daemon=True)
            for worker_id in worker_ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            self.store.update_run_sync_fields(run_id=run_id, sync_status="failed")
            self.store.update_asset_sync_job(
                job_id,
                status="failed",
                steps=steps,
                error_text="; ".join(errors),
                finished=True,
            )
            return

        self.store.update_run_sync_fields(run_id=run_id, sync_status="succeeded")
        self.store.update_asset_sync_job(job_id, status="succeeded", steps=steps, finished=True)

    def _sync_cases(self, entry: dict[str, Any], manifest: dict[str, Any]) -> None:
        dataset_path = Path(str(manifest["datasetPath"]))
        case_ids = list(entry["caseIds"])
        target_root = str(entry["targetRoot"])
        if entry["transport"] == "local":
            sync_cases_local(
                dataset_path=dataset_path,
                case_ids=case_ids,
                target_dataset_dir=Path(target_root) / "dataset",
            )
            return
        sync_cases_remote(
            ssh=self.ssh,
            host_alias=str(entry["sshHostAlias"]),
            dataset_path=dataset_path,
            case_ids=case_ids,
            target_root=target_root,
        )

    def _sync_bitfun(self, entry: dict[str, Any], manifest: dict[str, Any]) -> None:
        target_root = str(entry["targetRoot"])
        if entry["transport"] == "local":
            sync_bitfun_local(
                bitfun_cli_path=Path(str(manifest["bitfunCliPath"])),
                bitfun_config_dir=Path(str(manifest["bitfunConfigDir"])),
                target_bitfun_dir=Path(target_root) / "bitfun",
            )
            return
        sync_bitfun_remote(
            ssh=self.ssh,
            host_alias=str(entry["sshHostAlias"]),
            bitfun_cli_path=Path(str(manifest["bitfunCliPath"])),
            bitfun_config_dir=Path(str(manifest["bitfunConfigDir"])),
            target_root=target_root,
        )

    def cleanup_run_sync_assets(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return
        manifest = dict(run.get("sync_manifest") or {})
        workers = manifest.get("workers") or {}
        if not workers:
            return
        self.store.update_run_sync_fields(run_id=run_id, sync_status="cleaning")
        for worker_id, entry in workers.items():
            target_root = str(entry["targetRoot"])
            try:
                if entry.get("transport") == "local":
                    cleanup_sync_target_local(Path(target_root))
                else:
                    cleanup_sync_target_remote(
                        ssh=self.ssh,
                        host_alias=str(entry["sshHostAlias"]),
                        target_root=target_root,
                    )
            except Exception as exc:
                self._log(f"cleanup warning for {worker_id}: {exc}\n")
        self.store.update_run_sync_fields(run_id=run_id, sync_status="cleaned")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_asset_syncer.py::test_asset_syncer_promotes_batches_on_success -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_asset_syncer.py
git commit -m "feat: add AssetSyncer background job runner"
```

---

### Task 7: create-and-distribute API changes

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Create: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_create_task_sync_api.py`:

```python
import json
import os
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, tmp_path: Path, port: int) -> ThreadedServer:
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host test\n  HostName 127.0.0.1\n  User test\n", encoding="utf-8")
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config,
        controller_shared_root=tmp_path,
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = None
    Handler.asset_syncer = asset_syncer
    Handler.ssh_config_path = ssh_config
    Handler.controller_shared_root = tmp_path
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _prepare_assets(tmp_path: Path) -> dict[str, str]:
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()
    shared = tmp_path / "runtime"
    shared.mkdir()
    return {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(config_dir),
        "sharedRoot": str(shared),
    }


def test_create_task_rejects_remote_without_ssh(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="remote-a",
        display_name="remote",
        host="remote",
        slots_total=1,
        capabilities={"sharedRoot": "/nonexistent/runtime"},
    )
    server = start_test_server(store, tmp_path, 9881)
    conn = HTTPConnection("127.0.0.1", 9881)
    body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["remote-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 400
    payload = json.loads(resp.read().decode("utf-8"))
    assert "ssh_host_alias" in payload["error"]
    server.shutdown()


def test_create_task_local_worker_returns_pending_sync(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        capabilities={"sharedRoot": assets["sharedRoot"], "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9882)
    conn = HTTPConnection("127.0.0.1", 9882)
    body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["local-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["run"]["syncStatus"] == "pending"
    assert payload["syncJobId"]
    assert payload["batches"][0]["status"] == "pending_sync"
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_create_task_sync_api.py -v`
Expected: FAIL — 400 missing field `datasetRef` or `AttributeError: Handler has no attribute 'asset_syncer'`

- [ ] **Step 3: Write minimal implementation**

In `server.py`:

Add imports and class attributes:

```python
from agent_eval_orchestrator.controller.asset_syncer import (
    AssetSyncer,
    build_sync_manifest,
    validate_create_task_assets,
)
```

```python
class Handler(BaseHTTPRequestHandler):
    ...
    asset_syncer: AssetSyncer | None = None
    controller_shared_root: Path | None = None
```

Add helper `_build_asset_sync_executor_config(...)` that returns executor config with `useAssetSync: True` and empty per-worker path maps (filled after sync):

```python
def _build_asset_sync_executor_config(
    *,
    worker_ids: list[str],
    workers: list[dict[str, object]],
    body_config: dict[str, object],
    jobs_dir: str,
) -> dict[str, object]:
    workers_by_id = {str(worker["worker_id"]): worker for worker in workers}
    harbor_repo_by_worker = {
        worker_id: _default_harbor_for_worker(worker_id, workers_by_id.get(worker_id))
        for worker_id in worker_ids
    }
    uv_binary_by_worker = {
        worker_id: _default_uv_for_worker(worker_id, workers_by_id.get(worker_id))
        for worker_id in worker_ids
    }
    return {
        **_build_executor_config(
            dataset_ref="",
            worker_ids=worker_ids,
            workers=workers,
            body_config=body_config,
            jobs_dir=jobs_dir,
        ),
        "useAssetSync": True,
        "datasetPathByWorker": {},
        "mountsByWorker": {},
        "agentEnvByWorker": {},
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
    }
```

Replace `/api/eval-tasks/create-and-distribute` handler body:

```python
        if path == "/api/eval-tasks/create-and-distribute":
            try:
                owner = DEFAULT_OWNER
                dataset_path = Path(str(body["datasetPath"])).expanduser()
                bitfun_cli_path = Path(str(body["bitfunCliPath"])).expanduser()
                bitfun_config_dir = Path(str(body["bitfunConfigDir"])).expanduser()
                worker_ids = [
                    str(item).strip()
                    for item in body.get("workerIds") or []
                    if str(item).strip()
                ]
                case_ids = [
                    str(item).strip()
                    for item in body.get("selectedCaseIds") or []
                    if str(item).strip()
                ]
                if not case_ids:
                    case_ids = store.list_dataset_case_ids(str(dataset_path))
                workers = self.store.list_workers()
                controller_root = (self.controller_shared_root or self.store.layout.shared_root).expanduser()
                validate_create_task_assets(
                    dataset_path=dataset_path,
                    bitfun_cli_path=bitfun_cli_path,
                    bitfun_config_dir=bitfun_config_dir,
                    case_ids=case_ids,
                    workers=workers,
                    worker_ids=worker_ids,
                    controller_shared_root=controller_root,
                )
                jobs_dir = str(body.get("jobsDir") or DEFAULT_JOBS_DIR).strip() or str(DEFAULT_JOBS_DIR)
                body_config = dict(body.get("executorConfig") or {})
                executor_config = _build_asset_sync_executor_config(
                    worker_ids=worker_ids,
                    workers=workers,
                    body_config=body_config,
                    jobs_dir=jobs_dir,
                )
                task_name = str(body.get("name") or "").strip() or f"{dataset_path.name}-{now_iso()[:19]}"
                template = self.store.create_task_template(
                    owner=owner,
                    name=task_name,
                    dataset_ref=str(dataset_path),
                    executor_kind="harbor-docker",
                    executor_config=executor_config,
                    model_profile_ref=str(body.get("modelProfileRef") or "") or None,
                    note="",
                )
                run = self.store.create_run(template_id=str(template["template_id"]), display_name=task_name)
                batches = self.store.create_sharded_batches(
                    run_id=str(run["run_id"]),
                    selected_case_ids=case_ids,
                    worker_ids=worker_ids,
                    batch_options={
                        "concurrency": int(
                            body_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY
                        )
                    },
                    initial_status="pending_sync",
                )
                workers_by_id = {str(item["worker_id"]): item for item in workers}
                worker_shards = {
                    str(batch["preferred_worker_id"]): list(batch["selected_case_ids"])
                    for batch in batches
                }
                manifest = build_sync_manifest(
                    run_id=str(run["run_id"]),
                    dataset_path=dataset_path.resolve(),
                    bitfun_cli_path=bitfun_cli_path.resolve(),
                    bitfun_config_dir=bitfun_config_dir.resolve(),
                    worker_shards=worker_shards,
                    workers_by_id=workers_by_id,
                    controller_shared_root=controller_root,
                )
                sync_job_id = new_id("sync")
                self.store.update_run_sync_fields(
                    run_id=str(run["run_id"]),
                    sync_status="pending",
                    sync_job_id=sync_job_id,
                    sync_manifest=manifest,
                )
                self.store.create_asset_sync_job(
                    job_id=sync_job_id,
                    run_id=str(run["run_id"]),
                    steps=initial_worker_steps(worker_ids),
                )
                if self.asset_syncer is not None:
                    self.asset_syncer.start_job_async(
                        job_id=sync_job_id,
                        run_id=str(run["run_id"]),
                        template_id=str(template["template_id"]),
                    )
                run = self.store.get_run(str(run["run_id"])) or run
            except KeyError as exc:
                _json_response(self, {"error": f"missing field: {exc}"}, 400)
                return
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 400)
                return
            _json_response(
                self,
                {
                    "template": template,
                    "run": {
                        **run,
                        "syncStatus": run.get("sync_status") or "pending",
                    },
                    "batches": batches,
                    "syncJobId": sync_job_id,
                },
                201,
            )
            return
```

Wire in `main()`:

```python
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config_path,
        controller_shared_root=layout.shared_root,
    )
    ...
    Handler.asset_syncer = asset_syncer
    Handler.controller_shared_root = layout.shared_root
```

Add missing import: `from agent_eval_orchestrator.core.ids import new_id, now_iso` (if not already), and `initial_worker_steps`.

Normalize `_run_item` keys in response — snake_case from DB is fine; response uses camelCase for `syncStatus`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/controller/test_create_task_sync_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_create_task_sync_api.py
git commit -m "feat: create-and-distribute starts asset sync job"
```

---

### Task 8: Sync status GET endpoints

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_create_task_sync_api.py`:

```python
import time


def test_get_run_sync_status(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        capabilities={"sharedRoot": assets["sharedRoot"], "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9883)
    conn = HTTPConnection("127.0.0.1", 9883)
    create_body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["local-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=create_body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    created = json.loads(conn.getresponse().read().decode("utf-8"))
    run_id = created["run"]["run_id"]
    sync_job_id = created["syncJobId"]

    deadline = time.time() + 5
    status = "pending"
    while time.time() < deadline and status not in {"succeeded", "failed"}:
        conn.request(
            "GET",
            f"/api/runs/{run_id}/sync",
            headers={"X-AEO-Token": "secret"},
        )
        detail = json.loads(conn.getresponse().read().decode("utf-8"))
        status = detail["status"]
        time.sleep(0.2)

    assert status == "succeeded"
    conn.request(
        "GET",
        f"/api/sync-jobs/{sync_job_id}",
        headers={"X-AEO-Token": "secret"},
    )
    job = json.loads(conn.getresponse().read().decode("utf-8"))
    assert job["jobId"] == sync_job_id
    assert job["status"] == "succeeded"
    server.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_create_task_sync_api.py::test_get_run_sync_status -v`
Expected: FAIL — 404 not found

- [ ] **Step 3: Write minimal implementation**

In `server.py` `do_GET`, add before the 404 fallback:

```python
        if path.startswith("/api/runs/") and path.endswith("/sync"):
            run_id = path.split("/")[3]
            run = self.store.get_run(run_id)
            if not run:
                _json_response(self, {"error": "run not found"}, 404)
                return
            job = self.store.get_asset_sync_job_for_run(run_id)
            if not job:
                _json_response(self, {"error": "sync job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "runId": run_id,
                    "syncStatus": run.get("sync_status") or "",
                    "jobId": job["job_id"],
                    "status": job["status"],
                    "currentStep": job["current_step"],
                    "steps": job["steps"],
                    "logTail": job["log_tail"],
                    "errorText": job["error_text"],
                    "createdAt": job["created_at"],
                    "finishedAt": job["finished_at"],
                },
            )
            return
        if path.startswith("/api/sync-jobs/"):
            job_id = path.split("/")[3]
            job = self.store.get_asset_sync_job(job_id)
            if not job:
                _json_response(self, {"error": "job not found"}, 404)
                return
            _json_response(
                self,
                {
                    "jobId": job["job_id"],
                    "runId": job["run_id"],
                    "status": job["status"],
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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/controller/test_create_task_sync_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_create_task_sync_api.py
git commit -m "feat: add sync job status query endpoints"
```

---

### Task 9: Run terminal cleanup hook

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`is_run_terminal`)
- Modify: `src/agent_eval_orchestrator/controller/server.py` (hook after batch progress update)
- Modify: `tests/controller/test_asset_syncer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/controller/test_asset_syncer.py`:

```python
def test_cleanup_run_sync_assets_local(store, tmp_path, sample_ssh_config):
    shared = tmp_path / "runtime"
    shared.mkdir()
    target = shared / "sync" / "run-clean"
    (target / "dataset" / "case-a").mkdir(parents=True)
    template = store.create_task_template(
        owner="default",
        name="cleanup",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"], display_name="cleanup")
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "workers": {
                "local-a": {
                    "targetRoot": str(target),
                    "transport": "local",
                    "caseIds": ["case-a"],
                }
            }
        },
    )
    syncer = AssetSyncer(store=store, ssh_config_path=sample_ssh_config, controller_shared_root=tmp_path)
    syncer.cleanup_run_sync_assets(run["run_id"])
    assert not target.exists()
    updated = store.get_run(run["run_id"])
    assert updated["sync_status"] == "cleaned"
```

Add to `tests/storage/test_asset_sync_store.py`:

```python
def test_is_run_terminal(store):
    template = store.create_task_template(
        owner="default",
        name="term",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    assert store.is_run_terminal(run["run_id"]) is False
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
    )
    assert store.is_run_terminal(run["run_id"]) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_is_run_terminal tests/controller/test_asset_syncer.py::test_cleanup_run_sync_assets_local -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'is_run_terminal'`

- [ ] **Step 3: Write minimal implementation**

Add to `store.py`:

```python
    def is_run_terminal(self, run_id: str) -> bool:
        batches = self.list_batches_for_run(run_id)
        if not batches:
            return False
        terminal = {"succeeded", "failed", "stopped", "sync_failed"}
        return all(str(batch["status"]) in terminal for batch in batches)
```

In `server.py`, after successful `update_batch_progress` in the worker progress POST handler, add:

```python
            if self.asset_syncer is not None:
                batch = self.store.get_batch(batch_id)
                if batch and self.store.is_run_terminal(str(batch["run_id"])):
                    run = self.store.get_run(str(batch["run_id"]))
                    if run and str(run.get("sync_status") or "") in {"succeeded", "failed"}:
                        self.asset_syncer.cleanup_run_sync_assets(str(batch["run_id"]))
```

(Find the existing batch progress endpoint — search for `update_batch_progress` in `do_POST`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py tests/controller/test_asset_syncer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py src/agent_eval_orchestrator/controller/server.py tests/storage/test_asset_sync_store.py tests/controller/test_asset_syncer.py
git commit -m "feat: cleanup synced assets when run reaches terminal state"
```

---

### Task 10: Create Task UI — form fields

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Manual verification checklist (no automated UI test)**

Replace the Dataset Ref dropdown block (~line 572–580) with:

```html
            <div class="detail-grid" style="margin-bottom:16px">
              <div class="field">
                <label>Dataset Path</label>
                <input name="datasetPath" id="datasetPathInput" placeholder="/root/projects/agent-eval-orchestrator/datasets/swe-bench-verified" required />
              </div>
              <div class="field">
                <label>BitFun CLI Path</label>
                <input name="bitfunCliPath" value="/root/projects/BitFun/target/release/bitfun-cli" required />
              </div>
              <div class="field">
                <label>BitFun Config Dir</label>
                <input name="bitfunConfigDir" value="/root/.config/bitfun" required />
              </div>
              <div class="field">
                <label>Jobs Dir</label>
                <input name="jobsDir" value="/root/projects/harbor/jobs" required />
              </div>
            </div>
```

Update worker subtitle (~line 590):

```html
              <div class="subtle" style="margin-bottom:10px">勾选参与执行的 worker；创建后 controller 会将 dataset shard 与 bitfun-cli 同步到各 worker</div>
```

Update `collectCreateFormPayload`:

```javascript
    function collectCreateFormPayload(form) {
      const data = new FormData(form);
      const workerIds = data.getAll("workerIds").map(value => String(value));
      const selectedCaseIds = String(data.get("selectedCaseIds") || "")
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean);
      const concurrency = Number(data.get("nConcurrent") || 1);
      return {
        name: String(data.get("name") || "").trim(),
        datasetPath: String(data.get("datasetPath") || "").trim(),
        bitfunCliPath: String(data.get("bitfunCliPath") || "").trim(),
        bitfunConfigDir: String(data.get("bitfunConfigDir") || "").trim(),
        jobsDir: String(data.get("jobsDir") || "/root/projects/harbor/jobs").trim(),
        workerIds,
        selectedCaseIds,
        executorConfig: {
          agentName: "bitfun-cli",
          nConcurrent: concurrency,
          timeoutMultiplier: parsePositiveNumber(data.get("timeoutMultiplier"), 1.0),
          agentTimeoutMultiplier: parsePositiveNumber(data.get("agentTimeoutMultiplier"), 3.0),
          verifierTimeoutMultiplier: parsePositiveNumber(data.get("verifierTimeoutMultiplier"), 2.0),
          environmentBuildTimeoutMultiplier: parsePositiveNumber(
            data.get("environmentBuildTimeoutMultiplier"),
            1.5,
          ),
        },
      };
    }
```

Remove or stop calling `populateDatasetRefSelect()` if it only served the old dropdown; optionally pre-fill `datasetPathInput` from first preset in `state.datasets`.

- [ ] **Step 2: Run backend tests (regression)**

Run: `uv run --extra dev pytest tests/controller/test_create_task_sync_api.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: update create task form for asset sync paths"
```

---

### Task 11: Create Task UI — sync progress polling

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Implement sync progress UI**

Add state fields near other state init:

```javascript
      syncPollTimer: null,
      syncJob: null,
```

Replace `renderCreateResult` body to show sync progress when `syncJobId` present:

```javascript
    function renderSyncProgress(detail) {
      const workerBlocks = (detail.steps || []).map(workerEntry => {
        const steps = (workerEntry.steps || []).map(step =>
          '<div class="step-row">' +
            '<span>' + esc(step.label || step.id) + '</span>' +
            badge(step.status || "pending") +
          '</div>'
        ).join("");
        return '<div class="detail" style="margin-top:10px">' +
          '<div class="item-title"><strong>' + esc(workerEntry.workerId) + '</strong></div>' +
          steps +
        '</div>';
      }).join("");
      return '' +
        '<div class="item-title"><strong>资产同步中</strong>' + badge(detail.status || "pending") + '</div>' +
        workerBlocks +
        (detail.errorText ? '<pre class="error-text">' + esc(detail.errorText) + '</pre>' : '') +
        (detail.logTail ? '<pre class="log-tail">' + esc(detail.logTail.slice(-4000)) + '</pre>' : '');
    }

    async function pollSyncJob() {
      if (!state.syncJob?.runId) return;
      const detail = await api("/api/runs/" + encodeURIComponent(state.syncJob.runId) + "/sync");
      state.syncJob.detail = detail;
      const root = document.getElementById("createResult");
      if (root) {
        root.innerHTML = renderSyncProgress(detail);
      }
      if (["succeeded", "failed"].includes(detail.status)) {
        clearInterval(state.syncPollTimer);
        state.syncPollTimer = null;
        await loadDashboard();
      }
    }

    function startSyncPolling(runId) {
      state.syncJob = { runId };
      if (state.syncPollTimer) clearInterval(state.syncPollTimer);
      pollSyncJob();
      state.syncPollTimer = setInterval(pollSyncJob, 2500);
    }
```

Update `submitCreateTaskForm`:

```javascript
      state.createResult = result;
      renderCreateResult();
      if (result.syncJobId && result.run?.run_id) {
        startSyncPolling(result.run.run_id);
      } else {
        await loadDashboard();
      }
```

Update initial `renderCreateResult` success message to mention sync when `item.syncJobId` exists.

- [ ] **Step 2: Run backend tests (regression)**

Run: `uv run --extra dev pytest tests/ -v --ignore=tests/controller/test_harness.py -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: poll asset sync progress after task creation"
```

---

### Task 12: Run detail sync badge & summary counts

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py` (`list_eval_task_summaries`, `get_eval_task_detail`)
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_asset_sync_store.py`:

```python
def test_eval_task_summary_includes_sync_status(store):
    template = store.create_task_template(
        owner="default",
        name="summary",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
    )
    run = store.create_run(template_id=template["template_id"])
    store.update_run_sync_fields(run_id=run["run_id"], sync_status="running")
    summaries = store.list_eval_task_summaries()
    match = next(item for item in summaries if item["runId"] == run["run_id"])
    assert match["syncStatus"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_eval_task_summary_includes_sync_status -v`
Expected: FAIL — `KeyError: 'syncStatus'`

- [ ] **Step 3: Write minimal implementation**

In `list_eval_task_summaries`, extend each summary dict:

```python
                    "syncStatus": str(run.get("sync_status") or ""),
```

Update `status_counts` initialization to include `pending_sync` and `sync_failed`:

```python
            status_counts = {
                "queued": 0, "pending_sync": 0, "sync_failed": 0,
                "running": 0, "succeeded": 0, "failed": 0, "stopped": 0,
            }
```

Adjust `overall_status` logic: if any `pending_sync` and none running → `"syncing"`; if any `sync_failed` and none running/queued → reflect in status.

In `get_eval_task_detail`, include `"syncStatus": run.get("sync_status") or ""` in returned `run` object (already present if `_run_item` exposes it).

In `static.py` task list/detail rendering, add badge helper:

```javascript
    function syncStatusBadge(syncStatus) {
      if (!syncStatus) return "";
      const map = {
        pending: ["syncing", "warn"],
        running: ["syncing", "warn"],
        succeeded: ["ready", "ok"],
        failed: ["sync_failed", "danger"],
        cleaning: ["cleaning", "warn"],
        cleaned: ["cleaned", "ok"],
      };
      const [label, cls] = map[syncStatus] || [syncStatus, "warn"];
      return badge(label, cls);
    }
```

Show `syncStatusBadge(task.syncStatus)` in task list rows and eval task detail header.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py src/agent_eval_orchestrator/controller/static.py tests/storage/test_asset_sync_store.py
git commit -m "feat: expose sync status in eval task summaries and UI"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| Per-worker shard sync only | Task 4 (`build_sync_manifest`), Task 5 (`sync_cases_*`) |
| `datasetPath` + bitfun paths required | Task 4 validation, Task 7 API |
| Async job; batches `pending_sync` → `queued` | Task 2, Task 6, Task 7 |
| Reject remote workers without SSH | Task 4, Task 7 |
| Local copy via copytree | Task 5 |
| Remote rsync/scp over SSH | Task 3, Task 5 |
| Per-run `{sharedRoot}/sync/{runId}/` layout | Task 4 `_worker_target_root` |
| Executor config paths after sync | Task 2, Task 6 |
| GET sync endpoints | Task 8 |
| Partial failure handling | Task 6 (`errors` list, `sync_failed` batches) |
| Post-run cleanup | Task 9 |
| UI form + progress + badge | Task 10, Task 11, Task 12 |
| Extract SSH from provisioner | Task 3 |
| Tests per spec table | Tasks 1–9, 12 |

No spec gaps identified.

### Placeholder scan

No TBD/TODO/implement-later patterns in this plan.

### Type consistency

- `workerId` / `worker_id`: store uses snake_case internally; API/UI use camelCase in JSON responses — consistent with existing provision job endpoints.
- Batch statuses: `pending_sync`, `sync_failed`, `queued` used consistently.
- Run sync statuses: `pending`, `running`, `succeeded`, `failed`, `cleaning`, `cleaned` — match spec state machine.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-24-task-asset-sync.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
