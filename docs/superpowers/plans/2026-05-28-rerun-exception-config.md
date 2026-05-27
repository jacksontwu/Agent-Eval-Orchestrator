# Rerun Exception Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an operator clicks **重跑 Exception** on Task detail, show a pre-filled configuration modal, submit the edited config to `POST /api/runs/{runId}/rerun-exceptions`, persist it to the template and sync manifest, and then start the existing exception rerun flow.

**Architecture:** Keep rerun scope and worker assignment server-owned by deriving exception cases from `Store.group_exception_cases_by_worker()`. The frontend reuses the Create Task config field collection for a modal-only payload, while the backend validates optional config input before job creation and persists template/sync manifest changes in the existing SQLite store. Rerun asset sync remains scoped by `run_rerun_jobs.worker_shards`, and worker executor paths are refreshed after rerun sync.

**Tech Stack:** Python 3.10+, stdlib `http.server`/`sqlite3`/`pathlib`, existing `AssetSyncer`, embedded HTML/JS in `static.py`, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent_eval_orchestrator/controller/executor_config.py` | Shared Harbor executor config builder used by create-task and rerun config application |
| `src/agent_eval_orchestrator/controller/server.py` | Import shared config builder; pass request JSON to `RunRerunCoordinator.start_rerun()` |
| `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py` | Accept optional config body, validate assets, patch template/sync manifest, create rerun batches with configured concurrency |
| `src/agent_eval_orchestrator/controller/asset_syncer.py` | Refresh rerun worker executor paths after scoped rerun sync succeeds |
| `src/agent_eval_orchestrator/controller/static.py` | Rerun config modal, defaults builder, shared config payload collector, modal submit/error handling |
| `src/agent_eval_orchestrator/storage/store.py` | Add `update_task_template_dataset_ref()` helper |
| `tests/controller/test_executor_config_builder.py` | Unit coverage for shared executor config builder |
| `tests/storage/test_asset_sync_store.py` | Store helper regression coverage |
| `tests/controller/test_run_rerun_coordinator.py` | Coordinator config application and validation coverage |
| `tests/controller/test_rerun_exceptions_api.py` | POST body happy path and validation coverage |
| `tests/controller/test_asset_syncer_rerun.py` | Rerun sync executor path refresh coverage |

---

### Task 1: Shared Executor Config Builder

**Files:**
- Create: `src/agent_eval_orchestrator/controller/executor_config.py`
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Create: `tests/controller/test_executor_config_builder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/controller/test_executor_config_builder.py`:

```python
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config


def test_build_asset_sync_executor_config_uses_worker_defaults():
    config = build_asset_sync_executor_config(
        worker_ids=["local-a"],
        workers=[
            {
                "worker_id": "local-a",
                "capabilities": {
                    "sharedRoot": "/tmp/controller-runtime",
                    "localToController": True,
                },
            }
        ],
        body_config={
            "agentName": "bitfun-cli",
            "nConcurrent": 4,
            "timeoutMultiplier": 1.2,
            "agentTimeoutMultiplier": 3.5,
            "verifierTimeoutMultiplier": 2.5,
            "environmentBuildTimeoutMultiplier": 1.7,
            "maxRetries": 0,
        },
        jobs_dir="/tmp/harbor/jobs",
    )

    assert config["useAssetSync"] is True
    assert config["agentName"] == "bitfun-cli"
    assert config["nConcurrent"] == 4
    assert config["timeoutMultiplier"] == 1.2
    assert config["agentTimeoutMultiplier"] == 3.5
    assert config["verifierTimeoutMultiplier"] == 2.5
    assert config["environmentBuildTimeoutMultiplier"] == 1.7
    assert config["maxRetries"] == 0
    assert config["combinedJobsDir"] == "/tmp/harbor/jobs"
    assert config["datasetPathByWorker"] == {}
    assert config["mountsByWorker"] == {}
    assert config["harborRepoPathByWorker"]["local-a"].endswith("/harbor")
    assert config["uvBinaryByWorker"]["local-a"].endswith("/.local/bin/uv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/controller/test_executor_config_builder.py::test_build_asset_sync_executor_config_uses_worker_defaults -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.executor_config'`.

- [ ] **Step 3: Add the shared module**

Create `src/agent_eval_orchestrator/controller/executor_config.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_eval_orchestrator.core.defaults import (
    DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER,
    DEFAULT_ENVIRONMENT_DELETE,
    DEFAULT_ENVIRONMENT_FORCE_BUILD,
    DEFAULT_HARBOR_REPO,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PER_WORKER_CONCURRENCY,
    DEFAULT_TIMEOUT_MULTIPLIER,
    DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER,
)
from agent_eval_orchestrator.core.worker_paths import build_harbor_bind_mounts, default_bitfun_config_dir


DEFAULT_AGENT_NAME = "bitfun-cli"


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _worker_shared_root(worker: dict[str, Any] | None) -> str:
    if not worker:
        return ""
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    return str(capabilities.get("sharedRoot") or "").strip()


def _worker_repo_root(worker: dict[str, Any] | None) -> Path | None:
    shared_root = _worker_shared_root(worker)
    if not shared_root:
        return None
    from agent_eval_orchestrator.core.worker_paths import repo_root_from_shared_root

    return repo_root_from_shared_root(shared_root)


def _map_dataset_for_worker(dataset_ref: str, worker: dict[str, Any] | None) -> str:
    dataset_path = Path(dataset_ref).expanduser().resolve()
    repo_root = Path("/root/projects/agent-eval-orchestrator").resolve()
    worker_root = _worker_repo_root(worker)
    if worker_root and _is_subpath(dataset_path, repo_root):
        return str(worker_root / dataset_path.relative_to(repo_root))
    return str(dataset_path)


def _default_harbor_for_worker(worker_id: str, worker: dict[str, Any] | None) -> str:
    from agent_eval_orchestrator.core.worker_paths import default_harbor_repo_from_shared_root

    shared_root = _worker_shared_root(worker)
    if shared_root:
        harbor_path = default_harbor_repo_from_shared_root(shared_root)
        if harbor_path:
            return str(harbor_path)
    if worker_id == "remote-a":
        return "/home/wt/harbor"
    return str(DEFAULT_HARBOR_REPO)


def _default_uv_for_worker(worker_id: str, worker: dict[str, Any] | None) -> str:
    from agent_eval_orchestrator.core.worker_paths import default_uv_binary_from_shared_root

    if worker_id == "local-a":
        return "/root/.local/bin/uv"
    shared_root = _worker_shared_root(worker)
    if shared_root:
        uv_path = default_uv_binary_from_shared_root(shared_root)
        if uv_path:
            return str(uv_path)
    if worker_id == "remote-a":
        return "/home/wt/.local/bin/uv"
    return "/root/.local/bin/uv"


def _default_bitfun_mounts(worker_id: str, worker: dict[str, Any] | None) -> list[dict[str, Any]]:
    shared_root = _worker_shared_root(worker)
    harbor_repo = _default_harbor_for_worker(worker_id, worker)
    uv_binary = _default_uv_for_worker(worker_id, worker)
    bitfun_config = default_bitfun_config_dir(worker_id=worker_id, shared_root=shared_root or None)
    return build_harbor_bind_mounts(
        uv_binary=uv_binary,
        harbor_repo=harbor_repo,
        bitfun_config_dir=bitfun_config,
    )


def build_executor_config(
    *,
    dataset_ref: str,
    worker_ids: list[str],
    workers: list[dict[str, Any]],
    body_config: dict[str, Any],
    jobs_dir: str,
) -> dict[str, Any]:
    workers_by_id = {str(worker["worker_id"]): worker for worker in workers}
    harbor_repo_by_worker: dict[str, str] = {}
    dataset_path_by_worker: dict[str, str] = {}
    uv_binary_by_worker: dict[str, str] = {}
    mounts_by_worker: dict[str, list[dict[str, Any]]] = {}
    for worker_id in worker_ids:
        worker = workers_by_id.get(worker_id)
        harbor_repo_by_worker[worker_id] = _default_harbor_for_worker(worker_id, worker)
        dataset_path_by_worker[worker_id] = _map_dataset_for_worker(dataset_ref, worker)
        uv_binary_by_worker[worker_id] = _default_uv_for_worker(worker_id, worker)
        mounts_by_worker[worker_id] = _default_bitfun_mounts(worker_id, worker)

    n_concurrent = int(body_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY)
    timeout_multiplier = body_config.get("timeoutMultiplier")
    agent_timeout_multiplier = body_config.get("agentTimeoutMultiplier")
    verifier_timeout_multiplier = body_config.get("verifierTimeoutMultiplier")
    agent_setup_timeout_multiplier = body_config.get("agentSetupTimeoutMultiplier")
    environment_build_timeout_multiplier = body_config.get("environmentBuildTimeoutMultiplier")
    config: dict[str, Any] = {
        "agentName": str(body_config.get("agentName") or DEFAULT_AGENT_NAME),
        "envType": str(body_config.get("envType") or "docker"),
        "nConcurrent": n_concurrent,
        "timeoutMultiplier": (
            float(timeout_multiplier) if timeout_multiplier not in (None, "") else DEFAULT_TIMEOUT_MULTIPLIER
        ),
        "agentTimeoutMultiplier": (
            float(agent_timeout_multiplier)
            if agent_timeout_multiplier not in (None, "")
            else DEFAULT_AGENT_TIMEOUT_MULTIPLIER
        ),
        "verifierTimeoutMultiplier": (
            float(verifier_timeout_multiplier)
            if verifier_timeout_multiplier not in (None, "")
            else DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER
        ),
        "agentSetupTimeoutMultiplier": (
            float(agent_setup_timeout_multiplier) if agent_setup_timeout_multiplier not in (None, "") else None
        ),
        "environmentBuildTimeoutMultiplier": (
            float(environment_build_timeout_multiplier)
            if environment_build_timeout_multiplier not in (None, "")
            else DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
        ),
        "maxRetries": (
            int(body_config["maxRetries"])
            if body_config.get("maxRetries") not in (None, "")
            else DEFAULT_MAX_RETRIES
        ),
        "environmentForceBuild": (
            bool(body_config["environmentForceBuild"])
            if "environmentForceBuild" in body_config
            else DEFAULT_ENVIRONMENT_FORCE_BUILD
        ),
        "environmentDelete": (
            bool(body_config["environmentDelete"])
            if "environmentDelete" in body_config
            else DEFAULT_ENVIRONMENT_DELETE
        ),
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "datasetPathByWorker": dataset_path_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
        "mountsByWorker": mounts_by_worker,
        "collectJobs": True,
        "combinedJobsDir": jobs_dir,
    }
    for key in (
        "modelName",
        "modelNameByWorker",
        "agentKwargs",
        "agentKwargsByWorker",
        "agentEnv",
        "agentEnvByWorker",
        "processEnv",
        "processEnvByWorker",
        "extraArgs",
        "harborRepoPath",
        "datasetPath",
        "uvBinary",
        "mounts",
    ):
        if key in body_config and body_config[key] is not None:
            config[key] = body_config[key]
    return config


def build_asset_sync_executor_config(
    *,
    worker_ids: list[str],
    workers: list[dict[str, Any]],
    body_config: dict[str, Any],
    jobs_dir: str,
) -> dict[str, Any]:
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
        **build_executor_config(
            dataset_ref="",
            worker_ids=worker_ids,
            workers=workers,
            body_config=body_config,
            jobs_dir=jobs_dir,
        ),
        "useAssetSync": True,
        "datasetPathByWorker": {},
        "mountsByWorker": {},
        "harborRepoPathByWorker": harbor_repo_by_worker,
        "uvBinaryByWorker": uv_binary_by_worker,
    }
```

- [ ] **Step 4: Use the shared builder in create-task API**

In `src/agent_eval_orchestrator/controller/server.py`, add this import near the existing controller imports:

```python
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config
```

In `Handler.do_POST()` under `/api/eval-tasks/create-and-distribute`, replace:

```python
                executor_config = _build_asset_sync_executor_config(
                    worker_ids=worker_ids,
                    workers=workers,
                    body_config=body_config,
                    jobs_dir=jobs_dir,
                )
```

with:

```python
                executor_config = build_asset_sync_executor_config(
                    worker_ids=worker_ids,
                    workers=workers,
                    body_config=body_config,
                    jobs_dir=jobs_dir,
                )
```

- [ ] **Step 5: Run tests to verify shared builder and create path**

Run: `uv run --extra dev pytest tests/controller/test_executor_config_builder.py tests/controller/test_create_task_sync_api.py::test_create_task_local_worker_returns_pending_sync -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_eval_orchestrator/controller/executor_config.py src/agent_eval_orchestrator/controller/server.py tests/controller/test_executor_config_builder.py
git commit -m "refactor: share executor config builder"
```

---

### Task 2: Store Helper for Persisting Dataset Ref

**Files:**
- Modify: `src/agent_eval_orchestrator/storage/store.py`
- Modify: `tests/storage/test_asset_sync_store.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/storage/test_asset_sync_store.py`:

```python
def test_update_task_template_dataset_ref_preserves_executor_config(store):
    template = store.create_task_template(
        owner="default",
        name="cfg-dataset",
        dataset_ref="/tmp/old-dataset",
        executor_kind="harbor-docker",
        executor_config={
            "useAssetSync": True,
            "combinedJobsDir": "/tmp/harbor/jobs",
            "timeoutMultiplier": 1.5,
        },
        model_profile_ref=None,
        note="",
    )

    updated = store.update_task_template_dataset_ref(
        template["template_id"],
        "/tmp/new-dataset",
    )

    assert updated["dataset_ref"] == "/tmp/new-dataset"
    assert updated["executor_config"]["useAssetSync"] is True
    assert updated["executor_config"]["combinedJobsDir"] == "/tmp/harbor/jobs"
    assert updated["executor_config"]["timeoutMultiplier"] == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_update_task_template_dataset_ref_preserves_executor_config -v`

Expected: FAIL with `AttributeError: 'Store' object has no attribute 'update_task_template_dataset_ref'`.

- [ ] **Step 3: Add the store method**

In `src/agent_eval_orchestrator/storage/store.py`, add this method immediately after `update_task_template_executor_config()`:

```python
    def update_task_template_dataset_ref(
        self,
        template_id: str,
        dataset_ref: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_templates
                SET dataset_ref = ?, updated_at = ?
                WHERE template_id = ?
                """,
                (dataset_ref, now, template_id),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("template not found")
        updated = self.get_task_template(template_id)
        if not updated:
            raise RuntimeError("template not found after update")
        return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py::test_update_task_template_dataset_ref_preserves_executor_config -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/storage/store.py tests/storage/test_asset_sync_store.py
git commit -m "feat: update template dataset ref"
```

---

### Task 3: Coordinator Applies Optional Rerun Config

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Modify: `tests/controller/test_run_rerun_coordinator.py`
- Modify: `tests/controller/test_asset_syncer_rerun.py`

- [ ] **Step 1: Write coordinator config tests**

Add imports to `tests/controller/test_run_rerun_coordinator.py`:

```python
import os
```

Append these helpers and tests to `tests/controller/test_run_rerun_coordinator.py`:

```python
def _prepare_rerun_assets(tmp_path, case_ids):
    dataset = tmp_path / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    bitfun_config = tmp_path / "bitfun-config"
    bitfun_config.mkdir()
    jobs_dir = tmp_path / "harbor" / "jobs"
    return {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(bitfun_config),
        "jobsDir": str(jobs_dir),
    }


def _make_worker_local(store, tmp_path):
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={
            "sharedRoot": str(tmp_path / "shared"),
            "localToController": True,
        },
    )


def test_start_rerun_applies_config_and_updates_template_and_manifest(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    previous_target = str(tmp_path / "shared" / "sync" / run["run_id"])
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": "/tmp/old-dataset",
            "bitfunCliPath": "/tmp/old-bitfun-cli",
            "bitfunConfigDir": "/tmp/old-bitfun-config",
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a", "ok"],
                    "targetRoot": previous_target,
                    "transport": "local",
                }
            },
        },
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {
                "nConcurrent": 3,
                "timeoutMultiplier": 1.4,
                "agentTimeoutMultiplier": 3.4,
                "verifierTimeoutMultiplier": 2.4,
                "environmentBuildTimeoutMultiplier": 1.8,
            },
        },
    )

    assert result["exceptionCount"] == 1
    template = store.get_task_template(run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    executor_config = template["executor_config"]
    assert executor_config["nConcurrent"] == 3
    assert executor_config["timeoutMultiplier"] == 1.4
    assert executor_config["agentTimeoutMultiplier"] == 3.4
    assert executor_config["verifierTimeoutMultiplier"] == 2.4
    assert executor_config["environmentBuildTimeoutMultiplier"] == 1.8
    assert executor_config["combinedJobsDir"] == assets["jobsDir"]
    updated_run = store.get_run(run["run_id"])
    manifest = updated_run["sync_manifest"]
    assert manifest["datasetPath"] == assets["datasetPath"]
    assert manifest["bitfunCliPath"] == assets["bitfunCliPath"]
    assert manifest["bitfunConfigDir"] == assets["bitfunConfigDir"]
    assert manifest["workers"]["worker-a"]["targetRoot"] == previous_target
    assert manifest["workers"]["worker-a"]["transport"] == "local"
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_options"]["concurrency"] == 3


def test_start_rerun_config_validation_happens_before_job_creation(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, [])
    original_template = store.get_task_template(run["template_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": {"nConcurrent": 2},
            },
        )

    assert exc.value.code == 400
    assert "case directory not found: exc-a" in exc.value.message
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert store.get_task_template(run["template_id"])["dataset_ref"] == original_template["dataset_ref"]
```

- [ ] **Step 2: Write rerun sync path refresh test**

Append this test to `tests/controller/test_asset_syncer_rerun.py`:

```python
def test_sync_rerun_job_refreshes_executor_paths(store, tmp_path, sample_ssh_config):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    template = store.get_task_template(run["template_id"])
    target_root = str(tmp_path / "shared" / "sync" / run["run_id"])
    store.update_task_template_executor_config(
        str(template["template_id"]),
        {
            "useAssetSync": True,
            "uvBinaryByWorker": {"worker-a": "/usr/local/bin/uv"},
            "datasetPathByWorker": {},
            "mountsByWorker": {},
        },
    )
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": str(tmp_path / "dataset"),
            "bitfunCliPath": str(tmp_path / "bitfun-cli"),
            "bitfunConfigDir": str(tmp_path / "bitfun-config"),
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a"],
                    "targetRoot": target_root,
                    "transport": "local",
                }
            },
        },
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    job = store.create_run_rerun_job(
        job_id="rerun-paths",
        run_id=run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun["batch_id"]},
    )
    syncer = AssetSyncer(
        store=store,
        ssh_config_path=sample_ssh_config,
        controller_shared_root=tmp_path,
    )

    with patch.object(syncer, "_sync_cases"), patch.object(syncer, "_sync_bitfun"):
        syncer.sync_rerun_job(job_id=job["job_id"], run_id=run["run_id"])

    updated_template = store.get_task_template(run["template_id"])
    executor_config = updated_template["executor_config"]
    assert executor_config["datasetPathByWorker"]["worker-a"] == f"{target_root}/dataset"
    assert executor_config["mountsByWorker"]["worker-a"][0]["source"] == "/usr/local/bin/uv"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_applies_config_and_updates_template_and_manifest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_config_validation_happens_before_job_creation tests/controller/test_asset_syncer_rerun.py::test_sync_rerun_job_refreshes_executor_paths -v`

Expected: FAIL. The coordinator test fails with `TypeError: RunRerunCoordinator.start_rerun() got an unexpected keyword argument 'config'`. The asset syncer test fails because `datasetPathByWorker["worker-a"]` is missing.

- [ ] **Step 4: Implement config parsing and persistence in coordinator**

Replace `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py` with this content:

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_eval_orchestrator.controller.asset_syncer import build_sync_manifest, validate_create_task_assets
from agent_eval_orchestrator.controller.executor_config import build_asset_sync_executor_config
from agent_eval_orchestrator.core.defaults import (
    DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER,
    DEFAULT_HARBOR_REPO,
    DEFAULT_PER_WORKER_CONCURRENCY,
    DEFAULT_TIMEOUT_MULTIPLIER,
    DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER,
)
from agent_eval_orchestrator.core.ids import new_id

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
    from agent_eval_orchestrator.storage.store import Store


DEFAULT_JOBS_DIR = str(DEFAULT_HARBOR_REPO / "jobs")
CONFIG_KEYS = {"datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir", "executorConfig"}


class RerunValidationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _has_config(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    for key in CONFIG_KEYS:
        value = config.get(key)
        if value not in (None, "", {}):
            return True
    return False


def _required_text(value: Any, fallback: Any, field: str) -> str:
    raw = value if value not in (None, "") else fallback
    text = str(raw or "").strip()
    if not text:
        raise RerunValidationError(400, f"{field} is required")
    return text


def _positive_int(value: Any, fallback: int, field: str) -> int:
    raw = value if value not in (None, "") else fallback
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise RerunValidationError(400, f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise RerunValidationError(400, f"{field} must be a positive integer")
    return parsed


def _positive_float(value: Any, fallback: float, field: str) -> float:
    raw = value if value not in (None, "") else fallback
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise RerunValidationError(400, f"{field} must be a positive number") from exc
    if parsed <= 0:
        raise RerunValidationError(400, f"{field} must be a positive number")
    return parsed


class RunRerunCoordinator:
    def __init__(self, *, store: Store, asset_syncer: AssetSyncer | None) -> None:
        self.store = store
        self.asset_syncer = asset_syncer

    def start_rerun(self, run_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        rerun_status = str(run.get("rerun_status") or "idle")
        if rerun_status in {"syncing", "running"}:
            raise RerunValidationError(409, "rerun already in progress")
        grouped = self.store.group_exception_cases_by_worker(run_id)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")

        configured_batch_options = None
        if _has_config(config):
            configured_batch_options = self._apply_config_before_rerun(
                run=run,
                grouped=grouped,
                config=dict(config or {}),
            )

        job_id = new_id("rerun")
        rerun_batches: dict[str, str] = {}
        worker_shards: dict[str, list[str]] = {}
        all_case_ids: list[str] = []
        for worker_id, items in grouped.items():
            case_ids = [str(item["case_id"]) for item in items]
            parent_batch_id = str(items[0]["parent_batch_id"])
            parent = self.store.get_batch(parent_batch_id)
            batch_options = dict((parent or {}).get("batch_options") or {})
            if configured_batch_options is not None:
                batch_options.update(configured_batch_options)
            batch = self.store.create_batch(
                run_id=run_id,
                selected_case_ids=case_ids,
                preferred_worker_id=worker_id,
                batch_options=batch_options,
                initial_status="pending_sync",
                batch_kind="exception_rerun",
                parent_batch_id=parent_batch_id,
            )
            rerun_batches[worker_id] = str(batch["batch_id"])
            worker_shards[worker_id] = case_ids
            all_case_ids.extend(case_ids)

        self.store.create_run_rerun_job(
            job_id=job_id,
            run_id=run_id,
            case_ids=all_case_ids,
            worker_shards=worker_shards,
            rerun_batches=rerun_batches,
        )
        self.store.update_run_rerun_fields(
            run_id=run_id,
            rerun_status="syncing",
            rerun_job_id=job_id,
        )
        if self.asset_syncer is not None:
            self.asset_syncer.start_rerun_sync_async(job_id=job_id, run_id=run_id)

        return {
            "rerunJobId": job_id,
            "rerunStatus": "syncing",
            "exceptionCount": len(all_case_ids),
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in worker_shards.items()},
        }

    def _apply_config_before_rerun(
        self,
        *,
        run: dict[str, Any],
        grouped: dict[str, list[dict[str, Any]]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "template not found")
        existing_executor_config = dict(template.get("executor_config") or {})
        executor_body = dict(config.get("executorConfig") or {})
        manifest = dict(run.get("sync_manifest") or {})
        worker_ids = list(grouped.keys())
        worker_shards = {
            worker_id: [str(item["case_id"]) for item in items]
            for worker_id, items in grouped.items()
        }
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]

        dataset_path = Path(
            _required_text(
                config.get("datasetPath"),
                template.get("dataset_ref") or manifest.get("datasetPath"),
                "datasetPath",
            )
        ).expanduser()
        bitfun_cli_path = Path(
            _required_text(
                config.get("bitfunCliPath"),
                manifest.get("bitfunCliPath"),
                "bitfunCliPath",
            )
        ).expanduser()
        bitfun_config_dir = Path(
            _required_text(
                config.get("bitfunConfigDir"),
                manifest.get("bitfunConfigDir"),
                "bitfunConfigDir",
            )
        ).expanduser()
        jobs_dir = _required_text(
            config.get("jobsDir"),
            existing_executor_config.get("combinedJobsDir") or DEFAULT_JOBS_DIR,
            "jobsDir",
        )
        workers = self.store.list_workers()
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )
        try:
            validate_create_task_assets(
                dataset_path=dataset_path,
                bitfun_cli_path=bitfun_cli_path,
                bitfun_config_dir=bitfun_config_dir,
                case_ids=all_case_ids,
                workers=workers,
                worker_ids=worker_ids,
                controller_shared_root=controller_root,
            )
        except RuntimeError as exc:
            raise RerunValidationError(400, str(exc)) from exc

        existing_concurrency = int(
            existing_executor_config.get("nConcurrent")
            or self._primary_batch_options(grouped).get("concurrency")
            or DEFAULT_PER_WORKER_CONCURRENCY
        )
        n_concurrent = _positive_int(
            executor_body.get("nConcurrent"),
            existing_concurrency,
            "executorConfig.nConcurrent",
        )
        body_for_builder = dict(existing_executor_config)
        body_for_builder.update(executor_body)
        body_for_builder.update(
            {
                "agentName": str(existing_executor_config.get("agentName") or "bitfun-cli"),
                "nConcurrent": n_concurrent,
                "timeoutMultiplier": _positive_float(
                    executor_body.get("timeoutMultiplier"),
                    float(existing_executor_config.get("timeoutMultiplier") or DEFAULT_TIMEOUT_MULTIPLIER),
                    "executorConfig.timeoutMultiplier",
                ),
                "agentTimeoutMultiplier": _positive_float(
                    executor_body.get("agentTimeoutMultiplier"),
                    float(existing_executor_config.get("agentTimeoutMultiplier") or DEFAULT_AGENT_TIMEOUT_MULTIPLIER),
                    "executorConfig.agentTimeoutMultiplier",
                ),
                "verifierTimeoutMultiplier": _positive_float(
                    executor_body.get("verifierTimeoutMultiplier"),
                    float(existing_executor_config.get("verifierTimeoutMultiplier") or DEFAULT_VERIFIER_TIMEOUT_MULTIPLIER),
                    "executorConfig.verifierTimeoutMultiplier",
                ),
                "environmentBuildTimeoutMultiplier": _positive_float(
                    executor_body.get("environmentBuildTimeoutMultiplier"),
                    float(
                        existing_executor_config.get("environmentBuildTimeoutMultiplier")
                        or DEFAULT_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
                    ),
                    "executorConfig.environmentBuildTimeoutMultiplier",
                ),
            }
        )
        executor_config = build_asset_sync_executor_config(
            worker_ids=worker_ids,
            workers=workers,
            body_config=body_for_builder,
            jobs_dir=jobs_dir,
        )
        self.store.update_task_template_executor_config(str(template["template_id"]), executor_config)
        self.store.update_task_template_dataset_ref(str(template["template_id"]), str(dataset_path.resolve()))

        workers_by_id = {str(item["worker_id"]): item for item in workers}
        rebuilt_manifest = build_sync_manifest(
            run_id=str(run["run_id"]),
            dataset_path=dataset_path.resolve(),
            bitfun_cli_path=bitfun_cli_path.resolve(),
            bitfun_config_dir=bitfun_config_dir.resolve(),
            worker_shards=worker_shards,
            workers_by_id=workers_by_id,
            controller_shared_root=controller_root,
        )
        previous_workers = dict(manifest.get("workers") or {})
        next_workers = dict(previous_workers)
        for worker_id, rebuilt_entry in (rebuilt_manifest.get("workers") or {}).items():
            previous_entry = dict(previous_workers.get(worker_id) or {})
            next_entry = dict(rebuilt_entry)
            next_entry["caseIds"] = worker_shards[worker_id]
            next_entry["targetRoot"] = str(previous_entry.get("targetRoot") or rebuilt_entry["targetRoot"])
            next_entry["transport"] = str(previous_entry.get("transport") or rebuilt_entry["transport"])
            ssh_alias = previous_entry.get("sshHostAlias") or rebuilt_entry.get("sshHostAlias")
            if ssh_alias:
                next_entry["sshHostAlias"] = str(ssh_alias)
            next_workers[worker_id] = next_entry
        next_manifest = {
            **manifest,
            "datasetPath": str(dataset_path.resolve()),
            "bitfunCliPath": str(bitfun_cli_path.resolve()),
            "bitfunConfigDir": str(bitfun_config_dir.resolve()),
            "workers": next_workers,
        }
        self.store.update_run_sync_fields(
            run_id=str(run["run_id"]),
            sync_manifest=next_manifest,
        )
        return {"concurrency": n_concurrent}

    def _primary_batch_options(self, grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        for items in grouped.values():
            if not items:
                continue
            parent = self.store.get_batch(str(items[0]["parent_batch_id"]))
            if parent:
                return dict(parent.get("batch_options") or {})
        return {}
```

- [ ] **Step 5: Refresh executor paths after rerun sync**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, inside `AssetSyncer.sync_rerun_job()` in `worker_thread()`, add `uv_binary` and executor path update immediately after `entry = {**base_entry, "caseIds": case_ids}`:

```python
            uv_binary = str((executor_config.get("uvBinaryByWorker") or {}).get(worker_id) or "")
```

Then add this block after `self._sync_bitfun(entry, manifest)` and before the `"sync_bitfun"` status is set to `"succeeded"`:

```python
                if template:
                    paths = worker_executor_paths(
                        target_root=str(entry["targetRoot"]),
                        uv_binary=uv_binary,
                    )
                    self.store.update_task_template_executor_config(
                        str(template["template_id"]),
                        {
                            "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                            "mountsByWorker": {worker_id: paths["mounts"]},
                        },
                    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/controller/test_run_rerun_coordinator.py tests/controller/test_asset_syncer_rerun.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_eval_orchestrator/controller/run_rerun_coordinator.py src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_run_rerun_coordinator.py tests/controller/test_asset_syncer_rerun.py
git commit -m "feat: apply rerun exception config"
```

---

### Task 4: API Accepts Rerun Config Body

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Write API tests**

Add `os` to the imports in `tests/controller/test_rerun_exceptions_api.py`:

```python
import os
```

Append these helpers and tests to `tests/controller/test_rerun_exceptions_api.py`:

```python
def _prepare_rerun_assets(tmp_path, case_ids):
    dataset = tmp_path / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    bitfun_config = tmp_path / "bitfun-config"
    bitfun_config.mkdir()
    jobs_dir = tmp_path / "harbor" / "jobs"
    return {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(bitfun_config),
        "jobsDir": str(jobs_dir),
    }


def _make_worker_local(store, tmp_path):
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={
            "sharedRoot": str(tmp_path / "shared"),
            "localToController": True,
        },
    )


def test_post_rerun_exceptions_accepts_config_body(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    target_root = str(tmp_path / "shared" / "sync" / run["run_id"])
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": "/tmp/old-dataset",
            "bitfunCliPath": "/tmp/old-bitfun-cli",
            "bitfunConfigDir": "/tmp/old-bitfun-config",
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a"],
                    "targetRoot": target_root,
                    "transport": "local",
                }
            },
        },
    )
    server = start_test_server(store, tmp_path, 9895)
    conn = HTTPConnection("127.0.0.1", 9895)
    body = json.dumps(
        {
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {
                "nConcurrent": 2,
                "timeoutMultiplier": 1.3,
                "agentTimeoutMultiplier": 3.3,
                "verifierTimeoutMultiplier": 2.3,
                "environmentBuildTimeoutMultiplier": 1.6,
            },
        }
    )
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body=body,
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()

    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    template = store.get_task_template(run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    assert template["executor_config"]["nConcurrent"] == 2
    assert template["executor_config"]["combinedJobsDir"] == assets["jobsDir"]
    job = store.get_run_rerun_job(payload["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_options"]["concurrency"] == 2
    server.shutdown()


def test_post_rerun_exceptions_rejects_invalid_config_without_job(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    server = start_test_server(store, tmp_path, 9896)
    conn = HTTPConnection("127.0.0.1", 9896)
    body = json.dumps(
        {
            "datasetPath": str(tmp_path / "missing-dataset"),
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        }
    )

    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()

    assert resp.status == 400
    payload = json.loads(resp.read().decode("utf-8"))
    assert "datasetPath not found" in payload["error"]
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    server.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py::test_post_rerun_exceptions_accepts_config_body tests/controller/test_rerun_exceptions_api.py::test_post_rerun_exceptions_rejects_invalid_config_without_job -v`

Expected: FAIL. The happy path fails because `start_rerun()` is still called without the request body, and the invalid path returns 201 instead of 400.

- [ ] **Step 3: Pass request body to coordinator**

In `src/agent_eval_orchestrator/controller/server.py`, in the `POST /api/runs/{runId}/rerun-exceptions` branch, replace:

```python
                result = self.run_rerun_coordinator.start_rerun(run_id)
```

with:

```python
                result = self.run_rerun_coordinator.start_rerun(run_id, config=body)
```

- [ ] **Step 4: Run API tests**

Run: `uv run --extra dev pytest tests/controller/test_rerun_exceptions_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_rerun_exceptions_api.py
git commit -m "feat: accept rerun exception config body"
```

---

### Task 5: Rerun Config Modal UI

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`

- [ ] **Step 1: Add modal state**

In `src/agent_eval_orchestrator/controller/static.py`, add `rerunConfig` to the `state` object after `rerunPollTimer: null,`:

```javascript
      rerunPollTimer: null,
      rerunConfig: null,
```

- [ ] **Step 2: Add modal HTML**

Insert this modal before `<div class="toast hidden" id="toast"></div>`:

```html
  <div class="modal hidden" id="rerunConfigModal">
    <div class="modal-card">
      <div class="modal-header">
        <div>
          <h3>重跑 Exception 配置</h3>
          <div class="subtle" id="rerunConfigModalSubtitle">调整参数后开始重跑</div>
        </div>
        <button class="modal-close" id="rerunConfigModalClose" aria-label="关闭">×</button>
      </div>
      <div class="modal-body" id="rerunConfigModalBody"></div>
    </div>
  </div>
```

- [ ] **Step 3: Replace create payload collector with shared config collector**

Replace the existing `collectCreateFormPayload(form)` function with:

```javascript
    function collectTaskConfigPayload(form) {
      const data = new FormData(form);
      return {
        datasetPath: String(data.get("datasetPath") || "").trim(),
        bitfunCliPath: String(data.get("bitfunCliPath") || "").trim(),
        bitfunConfigDir: String(data.get("bitfunConfigDir") || "").trim(),
        jobsDir: String(data.get("jobsDir") || "/root/projects/harbor/jobs").trim(),
        executorConfig: {
          agentName: "bitfun-cli",
          nConcurrent: Number(data.get("nConcurrent") || 1),
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

    function collectCreateFormPayload(form) {
      const data = new FormData(form);
      const workerIds = data.getAll("workerIds").map(value => String(value));
      const selectedCaseIds = String(data.get("selectedCaseIds") || "")
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean);
      return {
        ...collectTaskConfigPayload(form),
        name: String(data.get("name") || "").trim(),
        workerIds,
        selectedCaseIds,
      };
    }
```

- [ ] **Step 4: Add defaults and modal render helpers**

Insert these functions immediately after `startSyncPolling(runId)`:

```javascript
    function firstDefined(...values) {
      for (const value of values) {
        if (value !== undefined && value !== null && value !== "") return value;
      }
      return "";
    }

    function rerunInvolvedWorkerCount(detail) {
      return (detail.workerGroups || []).filter(group =>
        (group.cases || []).some(item => caseIsErrored(item))
      ).length;
    }

    function buildRerunFormDefaults(detail) {
      const template = detail.template || {};
      const run = detail.run || {};
      const executorConfig = template.executor_config || {};
      const manifest = run.sync_manifest || {};
      const primaryBatch = (detail.batches || []).find(
        batch => String(batch.batch_kind || "primary") === "primary"
      ) || {};
      const batchOptions = primaryBatch.batch_options || {};
      return {
        executorKind: template.executor_kind || "harbor-docker",
        agentName: executorConfig.agentName || "bitfun-cli",
        nConcurrent: firstDefined(executorConfig.nConcurrent, batchOptions.concurrency, 1),
        timeoutMultiplier: firstDefined(executorConfig.timeoutMultiplier, 1.0),
        agentTimeoutMultiplier: firstDefined(executorConfig.agentTimeoutMultiplier, 3.0),
        verifierTimeoutMultiplier: firstDefined(executorConfig.verifierTimeoutMultiplier, 2.0),
        environmentBuildTimeoutMultiplier: firstDefined(executorConfig.environmentBuildTimeoutMultiplier, 1.5),
        datasetPath: firstDefined(template.dataset_ref, manifest.datasetPath),
        bitfunCliPath: firstDefined(manifest.bitfunCliPath),
        bitfunConfigDir: firstDefined(manifest.bitfunConfigDir),
        jobsDir: firstDefined(executorConfig.combinedJobsDir, "/root/projects/harbor/jobs"),
      };
    }

    function closeRerunConfigModal() {
      state.rerunConfig = null;
      document.getElementById("rerunConfigModal").classList.add("hidden");
    }

    function openRerunConfigModal(detail) {
      state.rerunConfig = {
        runId: detail.run.run_id,
        detail,
        defaults: buildRerunFormDefaults(detail),
        error: "",
        submitting: false,
      };
      renderRerunConfigModal();
      document.getElementById("rerunConfigModal").classList.remove("hidden");
    }

    function renderRerunConfigModal() {
      const modalState = state.rerunConfig;
      const body = document.getElementById("rerunConfigModalBody");
      if (!modalState) {
        body.innerHTML = "";
        return;
      }
      const detail = modalState.detail;
      const defaults = modalState.defaults;
      const submitLabel = modalState.submitting ? "提交中…" : "确认重跑";
      document.getElementById("rerunConfigModalSubtitle").textContent =
        (detail.run?.display_name || "-") + " · exception: " + (detail.exceptionCount || 0);
      body.innerHTML = '' +
        '<div class="detail-grid" style="margin-bottom:16px">' +
          '<div class="stat"><div class="subtle">Task</div><strong class="subtle">' + esc(detail.run?.display_name || "-") + '</strong></div>' +
          '<div class="stat"><div class="subtle">Exception cases</div><strong>' + esc(detail.exceptionCount || 0) + '</strong></div>' +
          '<div class="stat"><div class="subtle">Workers</div><strong>' + esc(rerunInvolvedWorkerCount(detail)) + '</strong></div>' +
        '</div>' +
        (modalState.error ? '<div class="empty" style="color:var(--bad);padding:10px 0">' + esc(modalState.error) + '</div>' : '') +
        '<form id="rerunConfigForm">' +
          '<div class="detail-grid" style="margin-bottom:16px">' +
            '<div class="field"><label>Executor</label><input name="executorKind" value="' + esc(defaults.executorKind) + '" readonly /></div>' +
            '<div class="field"><label>Agent Name</label><input name="agentName" value="' + esc(defaults.agentName) + '" readonly /></div>' +
            '<div class="field"><label>Per Worker Concurrency</label><input name="nConcurrent" type="number" min="1" value="' + esc(defaults.nConcurrent) + '" required /></div>' +
          '</div>' +
          '<div class="detail-grid" style="margin-bottom:16px">' +
            '<div class="field"><label>Timeout Multiplier</label><input name="timeoutMultiplier" type="number" min="0.1" step="0.1" value="' + esc(defaults.timeoutMultiplier) + '" /></div>' +
            '<div class="field"><label>Agent Timeout Multiplier</label><input name="agentTimeoutMultiplier" type="number" min="0.1" step="0.1" value="' + esc(defaults.agentTimeoutMultiplier) + '" /></div>' +
            '<div class="field"><label>Verifier Timeout Multiplier</label><input name="verifierTimeoutMultiplier" type="number" min="0.1" step="0.1" value="' + esc(defaults.verifierTimeoutMultiplier) + '" /></div>' +
            '<div class="field"><label>Environment Build Multiplier</label><input name="environmentBuildTimeoutMultiplier" type="number" min="0.1" step="0.1" value="' + esc(defaults.environmentBuildTimeoutMultiplier) + '" /></div>' +
          '</div>' +
          '<div class="detail-grid" style="margin-bottom:16px">' +
            '<div class="field"><label>Dataset Path</label><input name="datasetPath" value="' + esc(defaults.datasetPath) + '" required /></div>' +
            '<div class="field"><label>BitFun CLI Path</label><input name="bitfunCliPath" value="' + esc(defaults.bitfunCliPath) + '" required /></div>' +
            '<div class="field"><label>BitFun Config Dir</label><input name="bitfunConfigDir" value="' + esc(defaults.bitfunConfigDir) + '" required /></div>' +
            '<div class="field"><label>Jobs Dir</label><input name="jobsDir" value="' + esc(defaults.jobsDir) + '" required /></div>' +
          '</div>' +
          '<div class="actions">' +
            '<button class="primary" type="submit"' + (modalState.submitting ? ' disabled' : '') + '>' + submitLabel + '</button>' +
            '<button class="ghost" type="button" id="cancelRerunConfigBtn"' + (modalState.submitting ? ' disabled' : '') + '>取消</button>' +
          '</div>' +
        '</form>';
      document.getElementById("cancelRerunConfigBtn").addEventListener("click", closeRerunConfigModal);
      document.getElementById("rerunConfigForm").addEventListener("submit", submitRerunConfigForm);
    }

    async function submitRerunConfigForm(event) {
      event.preventDefault();
      const modalState = state.rerunConfig;
      if (!modalState || modalState.submitting) return;
      const payload = collectTaskConfigPayload(event.target);
      modalState.submitting = true;
      modalState.error = "";
      renderRerunConfigModal();
      try {
        await api("/api/runs/" + encodeURIComponent(modalState.runId) + "/rerun-exceptions", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        closeRerunConfigModal();
        if (state.rerunPollTimer) clearInterval(state.rerunPollTimer);
        state.rerunPollTimer = setInterval(() => pollRerunJob(modalState.runId), 2500);
        await pollRerunJob(modalState.runId);
      } catch (error) {
        if (state.rerunConfig) {
          state.rerunConfig.submitting = false;
          state.rerunConfig.error = formatApiError(error);
          renderRerunConfigModal();
        }
      }
    }
```

- [ ] **Step 5: Change rerun button flow to open modal**

Replace the body of `startRerunExceptions(runId, detail)` with:

```javascript
      const reason = rerunDisabledReason(detail);
      if (reason) {
        alert(reason);
        return;
      }
      openRerunConfigModal(detail);
```

- [ ] **Step 6: Bind modal close events**

Near the existing modal event listeners at the end of the script, add:

```javascript
    document.getElementById("rerunConfigModalClose").addEventListener("click", closeRerunConfigModal);
    document.getElementById("rerunConfigModal").addEventListener("click", (event) => {
      if (event.target.id === "rerunConfigModal" && !state.rerunConfig?.submitting) {
        closeRerunConfigModal();
      }
    });
```

- [ ] **Step 7: Run static syntax check**

Run: `uv run --extra dev python -m py_compile src/agent_eval_orchestrator/controller/static.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_eval_orchestrator/controller/static.py
git commit -m "feat: add rerun exception config modal"
```

---

### Task 6: Final Verification

**Files:**
- No new file changes expected

- [ ] **Step 1: Run focused test suite**

Run: `uv run --extra dev pytest tests/storage/test_asset_sync_store.py tests/controller/test_executor_config_builder.py tests/controller/test_run_rerun_coordinator.py tests/controller/test_asset_syncer_rerun.py tests/controller/test_rerun_exceptions_api.py tests/controller/test_create_task_sync_api.py -v`

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run: `uv run --extra dev pytest -v`

Expected: PASS.

- [ ] **Step 3: Manual UI verification**

Run the controller in the project’s normal way, open the dashboard, select a finished task with exception cases, and verify:

```text
1. Clicking 重跑 Exception opens 重跑 Exception 配置 instead of a browser confirm dialog.
2. The summary shows the task name, exception count, and only workers that have exception cases.
3. Executor and Agent Name are read-only.
4. Per Worker Concurrency, four multipliers, Dataset Path, BitFun CLI Path, BitFun Config Dir, and Jobs Dir are editable.
5. Submitting invalid paths shows an error inside the modal and keeps it open.
6. Submitting valid paths closes the modal and starts the existing rerun polling panel.
7. Reopening the modal after a successful submit shows the last submitted dataset/config values.
```

- [ ] **Step 4: Inspect git status**

Run: `git status --short`

Expected: clean working tree after the task commits, or only intentional uncommitted manual verification notes if the executor intentionally skipped commits.

---

## Self-Review Notes

Spec coverage:
- Modal on existing **重跑 Exception** button: Task 5.
- Read-only summary header: Task 5.
- Editable fields matching Create Task minus name/cases/workers: Task 5.
- Defaults from template, sync manifest, and primary batch options: Task 5.
- Request body extension and empty `{}` compatibility: Tasks 3 and 4.
- Persist template and sync manifest before rerun starts: Task 3.
- Configured rerun batch concurrency: Task 3.
- Asset validation before rerun job creation: Tasks 3 and 4.
- Preserve rerun scope and original worker assignment: Task 3 keeps grouping server-side.
- API 4xx stays in modal: Task 5.

Placeholder scan:
- The plan avoids placeholder markers and contains concrete tests, code blocks, commands, and expected outcomes for each task.

Type consistency:
- Frontend payload fields are `datasetPath`, `bitfunCliPath`, `bitfunConfigDir`, `jobsDir`, and `executorConfig`.
- Backend `RunRerunCoordinator.start_rerun(run_id, config=None)` matches the server call and existing tests that call `start_rerun(run_id)`.
- Store helper is consistently named `update_task_template_dataset_ref()`.
