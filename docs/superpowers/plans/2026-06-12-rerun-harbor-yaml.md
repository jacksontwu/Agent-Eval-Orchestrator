# Exception Rerun Harbor YAML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the field-based **Rerun Exception** configuration flow with a Harbor YAML-first flow where exception type selection controls scope and edited YAML controls runtime parameters.

**Architecture:** Add rerun-specific Harbor YAML parsing/building helpers that ignore user-submitted task ranges for scope, then wire them into `RunRerunCoordinator` preview and confirm flows. Reuse the existing YAML-first create-task asset planning and Harbor executor path so derived rerun batches execute via `harborYamlByBatchId`.

**Tech Stack:** Python 3.14, stdlib `dataclasses`/`pathlib`, PyYAML, existing SQLite `Store`, existing `AssetSyncer`, embedded static HTML/JavaScript, pytest.

---

## File Structure

- Modify `src/agent_eval_orchestrator/controller/harbor_yaml.py`
  - Add `parse_rerun_harbor_yaml()` for submitted rerun YAML templates.
  - Reuse `build_batch_harbor_yaml()` to generate final per-batch YAML.
  - Keep generic YAML parsing and path rewrite behavior in one YAML-focused module.

- Modify `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
  - Add preview method `preview_harbor_yaml()`.
  - Add a small `RerunScope` dataclass so scope resolution is shared by preview and confirm.
  - Add YAML-first confirm branch that creates derived run config, sync manifest, and `harborYamlByBatchId`.
  - Keep old structured config behavior for empty bodies and transition compatibility.

- Modify `src/agent_eval_orchestrator/controller/asset_syncer.py`
  - Teach `sync_rerun_job()` to sync generic bind assets from rerun manifests, matching `run_job()`.
  - Record `assetPathsByWorker` for rerun templates after rerun sync.

- Modify `src/agent_eval_orchestrator/controller/server.py`
  - Add `POST /api/runs/{runId}/rerun-exceptions/harbor-yaml-preview`.
  - Pass YAML-first confirm bodies through the coordinator.

- Modify `src/agent_eval_orchestrator/controller/static.py`
  - Replace old rerun form fields with a YAML editor.
  - Load preview when the modal opens.
  - Preserve edited YAML while exception type selection changes update preview stats.
  - Submit `{selectedErrorTypes, harborYaml}`.

- Modify tests:
  - `tests/controller/test_harbor_yaml.py`
  - `tests/controller/test_run_rerun_coordinator.py`
  - `tests/controller/test_asset_syncer.py`
  - `tests/controller/test_rerun_exceptions_api.py`
  - `tests/controller/test_static_auth_token.py`
  - `tests/executors/test_harbor_executor.py`

---

### Task 1: Add Rerun Harbor YAML Parser Helpers

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/harbor_yaml.py`
- Test: `tests/controller/test_harbor_yaml.py`

- [ ] **Step 1: Add failing dataset-mode tests**

Append these tests to `tests/controller/test_harbor_yaml.py`:

```python
def test_parse_rerun_dataset_yaml_ignores_submitted_task_names_and_builds_selected_yaml(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    for name in ("alpha", "beta", "gamma"):
        task_dir = dataset / name
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text("", encoding="utf-8")
    raw = f"""
job_name: original-job
jobs_dir: original-jobs
n_concurrent_trials: 7
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - gamma
"""

    plan = parse_rerun_harbor_yaml(raw, selected_task_ids=["alpha", "beta"], timestamp="20260612-120000")
    batch_yaml = build_batch_harbor_yaml(
        plan,
        batch_id="batch-a",
        selected_task_ids=["alpha"],
        jobs_dir=str(tmp_path / "jobs"),
        worker_dataset_path="/worker/sync/run-a/dataset",
    )
    payload = yaml.safe_load(batch_yaml)

    assert plan.mode == "datasets"
    assert plan.task_ids == ["alpha", "beta"]
    assert plan.dataset_ref == str(dataset.resolve())
    assert payload["job_name"] == "codex-openai-gpt-4o-tasks-20260612-120000"
    assert payload["jobs_dir"] == str(tmp_path / "jobs")
    assert payload["n_concurrent_trials"] == 7
    assert payload["datasets"][0]["path"] == "/worker/sync/run-a/dataset"
    assert payload["datasets"][0]["task_names"] == ["alpha"]
    assert "n_tasks" not in payload["datasets"][0]


def test_parse_rerun_dataset_yaml_rejects_selected_case_missing_from_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    (dataset / "alpha").mkdir(parents=True)
    (dataset / "alpha" / "task.toml").write_text("", encoding="utf-8")
    raw = f"""
agents:
  - name: codex
datasets:
  - path: {dataset}
    task_names:
      - alpha
"""

    with pytest.raises(HarborYamlError, match="case directory not found: beta"):
        parse_rerun_harbor_yaml(raw, selected_task_ids=["beta"], timestamp="20260612-120000")
```

Also update the import block in the same file:

```python
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlError,
    build_batch_harbor_yaml,
    parse_harbor_yaml,
    parse_rerun_harbor_yaml,
)
```

- [ ] **Step 2: Run dataset-mode tests and verify failure**

Run:

```bash
uv run pytest tests/controller/test_harbor_yaml.py::test_parse_rerun_dataset_yaml_ignores_submitted_task_names_and_builds_selected_yaml tests/controller/test_harbor_yaml.py::test_parse_rerun_dataset_yaml_rejects_selected_case_missing_from_dataset -q
```

Expected: FAIL with `ImportError: cannot import name 'parse_rerun_harbor_yaml'`.

- [ ] **Step 3: Add failing tasks-mode test**

Append this test to `tests/controller/test_harbor_yaml.py`:

```python
def test_parse_rerun_tasks_yaml_infers_selected_tasks_from_common_parent(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    for name in ("alpha", "beta", "gamma"):
        task_dir = dataset / name
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text("", encoding="utf-8")
    raw = f"""
job_name: user-job
agents:
  - name: codex
    model_name: openai/gpt-4o
tasks:
  - path: {dataset / "gamma"}
    metadata:
      keep: true
"""

    plan = parse_rerun_harbor_yaml(raw, selected_task_ids=["alpha", "beta"], timestamp="20260612-120000")
    batch_yaml = build_batch_harbor_yaml(
        plan,
        batch_id="batch-a",
        selected_task_ids=["beta"],
        jobs_dir=str(tmp_path / "jobs"),
        worker_dataset_path="/worker/sync/run-a/dataset",
    )
    payload = yaml.safe_load(batch_yaml)

    assert plan.mode == "tasks"
    assert plan.dataset_ref == str(dataset.resolve())
    assert plan.task_ids == ["alpha", "beta"]
    assert payload["tasks"] == [{"path": "/worker/sync/run-a/dataset/beta"}]
```

- [ ] **Step 4: Run tasks-mode test and verify failure**

Run:

```bash
uv run pytest tests/controller/test_harbor_yaml.py::test_parse_rerun_tasks_yaml_infers_selected_tasks_from_common_parent -q
```

Expected: FAIL with `ImportError: cannot import name 'parse_rerun_harbor_yaml'`.

- [ ] **Step 5: Implement `parse_rerun_harbor_yaml()`**

In `src/agent_eval_orchestrator/controller/harbor_yaml.py`, add `import os` near the top:

```python
import os
```

Then add these functions after `build_batch_harbor_yaml()`:

```python
def parse_rerun_harbor_yaml(
    raw_yaml: str,
    *,
    selected_task_ids: list[str],
    timestamp: str | None = None,
) -> HarborYamlPlan:
    raw_yaml = str(raw_yaml or "").strip()
    if not raw_yaml:
        raise HarborYamlError("harborYaml is required; paste valid Harbor YAML")
    selected = [str(item).strip() for item in selected_task_ids if str(item).strip()]
    if not selected:
        raise HarborYamlError("selected_task_ids must not be empty")
    try:
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise HarborYamlError(f"harborYaml must be valid Harbor YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise HarborYamlError("harborYaml top-level value must be a mapping")

    has_datasets = "datasets" in loaded
    has_tasks = "tasks" in loaded
    if has_datasets == has_tasks:
        raise HarborYamlError("harborYaml must contain exactly one of datasets or tasks")

    stamp = timestamp or safe_timestamp()
    if has_datasets:
        dataset_ref, task_ids, tasks_by_id = _resolve_rerun_dataset_tasks(loaded, selected)
        mode = "datasets"
    else:
        dataset_ref, task_ids, tasks_by_id = _resolve_rerun_direct_tasks(loaded, selected)
        mode = "tasks"

    generated_job_name = _generated_job_name(loaded, dataset_ref=dataset_ref, timestamp=stamp)
    return HarborYamlPlan(
        original_yaml=raw_yaml,
        original_config=deepcopy(loaded),
        mode=mode,
        dataset_ref=dataset_ref,
        task_ids=task_ids,
        generated_job_name=generated_job_name,
        timestamp=stamp,
        tasks_by_id=tasks_by_id,
    )
```

Add these helpers before `_resolve_dataset_tasks()`:

```python
def _resolve_rerun_dataset_tasks(
    config: dict[str, Any],
    selected_task_ids: list[str],
) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    datasets = config.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise HarborYamlError("datasets must be a non-empty list")
    if len(datasets) != 1:
        raise HarborYamlError("only one dataset entry is supported")
    dataset = datasets[0]
    if not isinstance(dataset, dict):
        raise HarborYamlError("datasets[0] must be a mapping")
    dataset_path = Path(str(dataset.get("path") or "")).expanduser().resolve()
    if not dataset_path.exists() or not dataset_path.is_dir():
        raise HarborYamlError(f"dataset path not found: {dataset_path}")
    for task_id in selected_task_ids:
        if not (dataset_path / task_id).is_dir():
            raise HarborYamlError(f"case directory not found: {task_id}")
    tasks_by_id = {
        task_id: {"path": str(dataset_path / task_id)}
        for task_id in selected_task_ids
    }
    return str(dataset_path), list(selected_task_ids), tasks_by_id


def _resolve_rerun_direct_tasks(
    config: dict[str, Any],
    selected_task_ids: list[str],
) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    tasks = config.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise HarborYamlError("tasks must be a non-empty list")
    task_by_name: dict[str, dict[str, Any]] = {}
    parent_paths: list[str] = []
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            raise HarborYamlError(f"tasks[{index}] must be a mapping")
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise HarborYamlError(f"task path not found: {path}")
        copied = deepcopy(item)
        copied["path"] = str(path)
        task_by_name[path.name] = copied
        parent_paths.append(str(path.parent))
    dataset_root = Path(os.path.commonpath(parent_paths)).resolve()
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for task_id in selected_task_ids:
        copied = deepcopy(task_by_name.get(task_id) or {"path": str(dataset_root / task_id)})
        path = Path(str(copied.get("path") or "")).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise HarborYamlError(f"task path not found: {task_id}")
        copied["path"] = str(path)
        tasks_by_id[task_id] = copied
    return str(dataset_root), list(selected_task_ids), tasks_by_id
```

- [ ] **Step 6: Run Harbor YAML tests**

Run:

```bash
uv run pytest tests/controller/test_harbor_yaml.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add src/agent_eval_orchestrator/controller/harbor_yaml.py tests/controller/test_harbor_yaml.py
git commit -m "feat: add rerun Harbor YAML parsing"
```

Expected: commit succeeds.

---

### Task 2: Add Coordinator Preview and Legacy YAML Generation

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Test: `tests/controller/test_run_rerun_coordinator.py`

- [ ] **Step 1: Add failing preview tests**

Append these helpers and tests to `tests/controller/test_run_rerun_coordinator.py`:

```python
def _make_dataset(tmp_path, case_ids):
    dataset = tmp_path / "dataset"
    for case_id in case_ids:
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    return dataset


def test_preview_harbor_yaml_returns_original_yaml_and_scope_stats(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "exc-b", "ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "timeout", "metrics": {"errorType": "AgentTimeoutError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    yaml_text = f"""
job_name: original
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - ok
"""
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    store.update_task_template_executor_config(
        run["template_id"],
        {
            "harborYaml": yaml_text,
            "harborYamlMode": "datasets",
            "harborYamlTaskIds": ["exc-a", "exc-b", "ok"],
            "combinedJobsDir": "",
        },
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    preview = coordinator.preview_harbor_yaml(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"]},
    )

    assert preview["source"] == "original_yaml"
    assert preview["harborYaml"].strip() == yaml_text.strip()
    assert preview["exceptionCount"] == 1
    assert preview["selectedErrorTypes"] == ["stderr"]
    assert preview["workerShards"] == {"worker-a": 1}


def test_preview_harbor_yaml_generates_legacy_yaml(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    store.update_task_template_executor_config(
        run["template_id"],
        {
            "agentName": "bitfun-cli",
            "modelName": "deepseek-v4-pro",
            "nConcurrent": 6,
            "timeoutMultiplier": 1.25,
            "agentTimeoutMultiplier": 3,
            "verifierTimeoutMultiplier": 2,
            "environmentBuildTimeoutMultiplier": 1.5,
            "envType": "docker",
        },
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    preview = coordinator.preview_harbor_yaml(run["run_id"], config={"selectedErrorTypes": ["stderr"]})
    payload = yaml.safe_load(preview["harborYaml"])

    assert preview["source"] == "generated_legacy_yaml"
    assert payload["n_concurrent_trials"] == 6
    assert payload["timeout_multiplier"] == 1.25
    assert payload["agent_timeout_multiplier"] == 3
    assert payload["verifier_timeout_multiplier"] == 2
    assert payload["environment_build_timeout_multiplier"] == 1.5
    assert payload["agents"] == [{"name": "bitfun-cli", "model_name": "deepseek-v4-pro"}]
    assert payload["environment"] == {"type": "docker"}
    assert payload["datasets"][0]["path"] == str(dataset)
    assert payload["datasets"][0]["task_names"] == ["exc-a", "ok"]
```

Add these imports near the top of the test file:

```python
from pathlib import Path

import yaml
```

- [ ] **Step 2: Run preview tests and verify failure**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py::test_preview_harbor_yaml_returns_original_yaml_and_scope_stats tests/controller/test_run_rerun_coordinator.py::test_preview_harbor_yaml_generates_legacy_yaml -q
```

Expected: FAIL with `AttributeError: 'RunRerunCoordinator' object has no attribute 'preview_harbor_yaml'`.

- [ ] **Step 3: Add imports and `RerunScope`**

In `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`, add imports:

```python
from dataclasses import dataclass, replace

import yaml
```

Extend the Harbor YAML imports:

```python
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlPlan,
    build_batch_harbor_yaml,
    discover_bind_assets,
    parse_rerun_harbor_yaml,
)
```

Add this dataclass after `RERUN_SCOPE_KEYS`:

```python
@dataclass(frozen=True)
class RerunScope:
    exception_items: list[dict[str, Any]]
    selected_error_types: list[str]
    grouped: dict[str, list[dict[str, Any]]]
    worker_shards: dict[str, list[str]]
    all_case_ids: list[str]
    dataset_path: Path
```

- [ ] **Step 4: Add shared scope and preview helpers**

In `RunRerunCoordinator`, add these methods before `_mark_derived_rerun_failed()`:

```python
    def preview_harbor_yaml(self, run_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RerunValidationError(404, "run not found")
        if not self.store.is_run_primary_terminal(run_id):
            raise RerunValidationError(409, "run not finished")
        source_template = self.store.get_task_template(str(run["template_id"]))
        if not source_template:
            raise RerunValidationError(404, "task template not found")
        scope = self._resolve_rerun_scope(
            run=run,
            template=source_template,
            config=config,
        )
        harbor_yaml, source = self._preview_harbor_yaml_for_run(run=run, template=source_template)
        return {
            "harborYaml": harbor_yaml,
            "source": source,
            "exceptionCount": len(scope.all_case_ids),
            "selectedErrorTypes": scope.selected_error_types,
            "workerShards": {worker_id: len(case_ids) for worker_id, case_ids in scope.worker_shards.items()},
        }

    def _resolve_rerun_scope(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
        config: dict[str, Any] | None,
        dataset_path: Path | None = None,
    ) -> RerunScope:
        existing_manifest = dict(run.get("sync_manifest") or {})
        resolved_dataset_path = dataset_path or self._resolve_dataset_path(
            config=config,
            template=template,
            existing_manifest=existing_manifest,
        )
        exception_items = self._list_rerun_exception_items(
            run=run,
            template=template,
            dataset_path=resolved_dataset_path,
        )
        selected_error_types = self._resolve_selected_error_types_from_items(
            exception_items,
            config=config,
        )
        if not selected_error_types:
            raise RerunValidationError(400, "no exception cases")
        selected_set = set(selected_error_types)
        filtered_items = [
            item for item in exception_items if str(item.get("error_type") or "") in selected_set
        ]
        if not filtered_items:
            raise RerunValidationError(400, "no matching exception cases")
        grouped = self.store.group_exception_items_by_worker(filtered_items)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")
        worker_shards = self._resolve_worker_shards(grouped, resolved_dataset_path)
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]
        return RerunScope(
            exception_items=filtered_items,
            selected_error_types=selected_error_types,
            grouped=grouped,
            worker_shards=worker_shards,
            all_case_ids=all_case_ids,
            dataset_path=resolved_dataset_path,
        )

    def _preview_harbor_yaml_for_run(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
    ) -> tuple[str, str]:
        executor_config = dict(template.get("executor_config") or {})
        raw_yaml = str(executor_config.get("harborYaml") or "").strip()
        if raw_yaml:
            return raw_yaml, "original_yaml"
        return self._build_legacy_rerun_harbor_yaml(run=run, template=template), "generated_legacy_yaml"

    def _build_legacy_rerun_harbor_yaml(
        self,
        *,
        run: dict[str, Any],
        template: dict[str, Any],
    ) -> str:
        executor_config = dict(template.get("executor_config") or {})
        agent: dict[str, Any] = {"name": str(executor_config.get("agentName") or "bitfun-cli")}
        model_name = str(executor_config.get("modelName") or "").strip()
        if model_name:
            agent["model_name"] = model_name
        payload: dict[str, Any] = {
            "job_name": sanitize_name(str(run.get("display_name") or template.get("name") or "rerun")),
            "jobs_dir": str(executor_config.get("combinedJobsDir") or DEFAULT_HARBOR_REPO / "jobs"),
            "n_concurrent_trials": int(executor_config.get("nConcurrent") or DEFAULT_PER_WORKER_CONCURRENCY),
            "agents": [agent],
            "datasets": [
                {
                    "path": str(template.get("dataset_ref") or ""),
                    "task_names": self._source_run_case_ids(str(run["run_id"])),
                }
            ],
        }
        mapping = {
            "timeoutMultiplier": "timeout_multiplier",
            "agentTimeoutMultiplier": "agent_timeout_multiplier",
            "verifierTimeoutMultiplier": "verifier_timeout_multiplier",
            "environmentBuildTimeoutMultiplier": "environment_build_timeout_multiplier",
        }
        for source_key, yaml_key in mapping.items():
            value = executor_config.get(source_key)
            if value not in (None, ""):
                payload[yaml_key] = value
        env_type = str(executor_config.get("envType") or "").strip()
        mounts = executor_config.get("mounts")
        environment: dict[str, Any] = {}
        if env_type:
            environment["type"] = env_type
        if isinstance(mounts, list) and mounts:
            environment["mounts"] = mounts
        if environment:
            payload["environment"] = environment
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    def _source_run_case_ids(self, run_id: str) -> list[str]:
        case_ids: list[str] = []
        for batch in self.store.list_primary_batches_for_run(run_id):
            for case_id in batch.get("selected_case_ids") or []:
                value = str(case_id or "").strip()
                if value and value not in case_ids:
                    case_ids.append(value)
        return case_ids
```

- [ ] **Step 5: Refactor `start_rerun()` to use shared scope**

In `start_rerun()`, replace the block from:

```python
        existing_manifest = dict(run.get("sync_manifest") or {})
        dataset_path = self._resolve_dataset_path(
            config=config,
            template=source_template,
            existing_manifest=existing_manifest,
        )

        exception_items = self._list_rerun_exception_items(
            run=run,
            template=source_template,
            dataset_path=dataset_path,
        )
        selected_error_types = self._resolve_selected_error_types_from_items(
            exception_items,
            config=config,
        )
        if not selected_error_types:
            raise RerunValidationError(400, "no exception cases")

        selected_set = set(selected_error_types)
        filtered_items = [
            item for item in exception_items if str(item.get("error_type") or "") in selected_set
        ]
        if not filtered_items:
            raise RerunValidationError(400, "no matching exception cases")

        grouped = self.store.group_exception_items_by_worker(filtered_items)
        if not grouped:
            raise RerunValidationError(400, "no exception cases")

        worker_shards = self._resolve_worker_shards(grouped, dataset_path)
        all_case_ids = [
            case_id
            for case_ids in worker_shards.values()
            for case_id in case_ids
        ]
```

with:

```python
        existing_manifest = dict(run.get("sync_manifest") or {})
        scope = self._resolve_rerun_scope(
            run=run,
            template=source_template,
            config=config,
        )
        grouped = scope.grouped
        worker_shards = scope.worker_shards
        all_case_ids = scope.all_case_ids
        selected_error_types = scope.selected_error_types
```

- [ ] **Step 6: Run coordinator preview tests**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py::test_preview_harbor_yaml_returns_original_yaml_and_scope_stats tests/controller/test_run_rerun_coordinator.py::test_preview_harbor_yaml_generates_legacy_yaml -q
```

Expected: PASS.

- [ ] **Step 7: Run existing coordinator rerun regression tests**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add src/agent_eval_orchestrator/controller/run_rerun_coordinator.py tests/controller/test_run_rerun_coordinator.py
git commit -m "feat: add rerun Harbor YAML preview"
```

Expected: commit succeeds.

---

### Task 3: Apply YAML-First Config During Derived Rerun Creation

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`
- Test: `tests/controller/test_run_rerun_coordinator.py`

- [ ] **Step 1: Add failing YAML-first confirm tests**

Append these tests to `tests/controller/test_run_rerun_coordinator.py`:

```python
def test_start_rerun_harbor_yaml_ignores_submitted_task_names_and_writes_batch_yaml(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "exc-b", "ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "timeout", "metrics": {"errorType": "AgentTimeoutError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    submitted_yaml = f"""
job_name: user-edited-job
jobs_dir: user-jobs
n_concurrent_trials: 9
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - ok
"""
    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
    )

    derived_run = store.get_run(result["runId"])
    derived_template = store.get_task_template(derived_run["template_id"])
    executor_config = derived_template["executor_config"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    batch_yaml = yaml.safe_load(executor_config["harborYamlByBatchId"][rerun_batch["batch_id"]])

    assert result["exceptionCount"] == 1
    assert rerun_batch["selected_case_ids"] == ["exc-a"]
    assert executor_config["harborYaml"] == submitted_yaml.strip()
    assert executor_config["harborYamlMode"] == "datasets"
    assert executor_config["harborYamlTaskIds"] == ["exc-a"]
    assert executor_config["harborYamlGeneratedJobName"] == sanitize_name(derived_run["display_name"])
    assert executor_config["combinedJobsDir"] == str(derived_jobs_dir_for_run(store=store, run=derived_run))
    assert batch_yaml["n_concurrent_trials"] == 9
    assert batch_yaml["agents"][0]["name"] == "codex"
    assert batch_yaml["datasets"][0]["task_names"] == ["exc-a"]
    assert batch_yaml["job_name"] == sanitize_name(derived_run["display_name"])
    assert batch_yaml["jobs_dir"] == str(Path(rerun_batch["batch_root"]) / "harbor" / "jobs")


def test_start_rerun_harbor_yaml_rejects_changed_dataset_missing_selected_case(store, tmp_path):
    source_dataset = _make_dataset(tmp_path / "source", ["exc-a", "ok"])
    target_dataset = _make_dataset(tmp_path / "target", ["ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(source_dataset))
    submitted_yaml = f"""
agents:
  - name: codex
datasets:
  - path: {target_dataset}
    task_names:
      - ok
"""

    with pytest.raises(RerunValidationError) as exc:
        RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
            run["run_id"],
            config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
        )

    assert exc.value.code == 400
    assert exc.value.message == "case directory not found: exc-a"
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_harbor_yaml_manifest_includes_bind_assets(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "ok"])
    codeagent = tmp_path / "codeagentcli"
    codeagent.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(codeagent, 0o755)
    _make_worker_local(store, tmp_path)
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    submitted_yaml = f"""
agents:
  - name: codeagent
    kwargs:
      binary_path: {codeagent}
datasets:
  - path: {dataset}
    task_names:
      - ok
environment:
  type: docker
  mounts:
    - type: bind
      source: {codeagent}
      target: /usr/local/bin/codeagentcli
"""

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
    )

    derived_run = store.get_run(result["runId"])
    manifest = derived_run["sync_manifest"]
    assert manifest["datasetPath"] == str(dataset.resolve())
    assert manifest["bindAssets"] == [
        {"source": str(codeagent.resolve()), "kind": "file", "targetName": "codeagentcli"}
    ]
```

- [ ] **Step 2: Run YAML-first confirm tests and verify failure**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_ignores_submitted_task_names_and_writes_batch_yaml tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_rejects_changed_dataset_missing_selected_case tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_manifest_includes_bind_assets -q
```

Expected: FAIL because `start_rerun()` ignores `harborYaml`.

- [ ] **Step 3: Add YAML-first helpers in the coordinator**

In `src/agent_eval_orchestrator/controller/run_rerun_coordinator.py`, add these imports:

```python
from agent_eval_orchestrator.controller.asset_syncer import (
    build_sync_manifest,
    validate_create_task_assets,
    validate_dataset_assets,
)
```

Replace the existing single-line asset sync import with the block above if needed.

Add this method before `_prevalidate_config()`:

```python
    def _parse_harbor_yaml_config(
        self,
        *,
        config: dict[str, Any] | None,
        selected_task_ids: list[str],
    ) -> HarborYamlPlan | None:
        if not isinstance(config, dict) or "harborYaml" not in config:
            return None
        raw_yaml = str(config.get("harborYaml") or "").strip()
        try:
            return parse_rerun_harbor_yaml(raw_yaml, selected_task_ids=selected_task_ids)
        except ValueError as exc:
            raise RerunValidationError(400, str(exc)) from exc
```

Add this method before `_set_derived_template_jobs_dir()`:

```python
    def _apply_harbor_yaml_config(
        self,
        *,
        run: dict[str, Any],
        plan: HarborYamlPlan,
        worker_shards: dict[str, list[str]],
        rerun_batch_case_ids: dict[str, list[str]],
    ) -> None:
        template = self.store.get_task_template(str(run["template_id"]))
        if not template:
            raise RerunValidationError(404, "task template not found")
        controller_root = (
            self.asset_syncer.controller_shared_root
            if self.asset_syncer is not None
            else self.store.layout.root
        )
        workers = self.store.list_workers()
        workers_by_id = {str(item["worker_id"]): item for item in workers}
        worker_ids = list(worker_shards.keys())
        task_sources = (
            {task_id: str(task["path"]) for task_id, task in plan.tasks_by_id.items()}
            if plan.mode == "tasks"
            else None
        )
        try:
            bind_assets = discover_bind_assets(plan.original_config)
            validate_dataset_assets(
                dataset_path=Path(plan.dataset_ref),
                case_ids=plan.task_ids,
                workers=workers,
                worker_ids=worker_ids,
                controller_shared_root=controller_root,
                task_sources=task_sources,
            )
            manifest = build_sync_manifest(
                run_id=str(run["run_id"]),
                dataset_path=Path(plan.dataset_ref),
                worker_shards=worker_shards,
                workers_by_id=workers_by_id,
                controller_shared_root=controller_root,
                bind_assets=bind_assets,
                task_sources=task_sources,
            )
        except (RuntimeError, ValueError) as exc:
            raise RerunValidationError(400, str(exc)) from exc
        runtime_job_name = sanitize_name(str(run["display_name"]))
        runtime_plan = replace(plan, generated_job_name=runtime_job_name)
        yaml_by_batch_id: dict[str, str] = {}
        for batch_id, case_ids in rerun_batch_case_ids.items():
            batch = self.store.get_batch(batch_id)
            if not batch:
                raise RerunValidationError(404, f"rerun batch not found: {batch_id}")
            worker_id = str(batch.get("preferred_worker_id") or batch.get("assigned_worker_id") or "")
            worker_sync_root = str(manifest["workers"][worker_id]["targetRoot"])
            worker_dataset_path = str(Path(worker_sync_root) / "dataset")
            try:
                yaml_by_batch_id[batch_id] = build_batch_harbor_yaml(
                    runtime_plan,
                    batch_id=batch_id,
                    selected_task_ids=case_ids,
                    jobs_dir=str(Path(str(batch["batch_root"])) / "harbor" / "jobs"),
                    worker_dataset_path=worker_dataset_path,
                    worker_sync_root=worker_sync_root,
                    bind_assets=bind_assets,
                )
            except ValueError as exc:
                raise RerunValidationError(400, str(exc)) from exc
        self.store.update_task_template_executor_config(
            str(template["template_id"]),
            {
                "harborYaml": plan.original_yaml,
                "harborYamlMode": plan.mode,
                "harborYamlTaskIds": plan.task_ids,
                "harborYamlGeneratedJobName": runtime_job_name,
                "harborYamlByBatchId": yaml_by_batch_id,
                "collectJobs": True,
                "combinedJobsDir": str(derived_jobs_dir_for_run(store=self.store, run=run)),
            },
            replace_keys={"harborYamlByBatchId"},
        )
        self.store.update_task_template_dataset_ref(str(template["template_id"]), plan.dataset_ref)
        self.store.update_run_sync_fields(
            run_id=str(run["run_id"]),
            sync_manifest=manifest,
        )
```

- [ ] **Step 4: Wire YAML-first branch into `start_rerun()`**

After `config_supplied = self._has_applicable_config(asset_config)`, add:

```python
        harbor_yaml_plan = self._parse_harbor_yaml_config(
            config=config,
            selected_task_ids=all_case_ids,
        )
```

Change the prevalidation block from:

```python
        if config_supplied:
            self._prevalidate_config(
                config=dict(asset_config or {}),
                template=source_template,
                fallback_manifest=existing_manifest,
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )
```

to:

```python
        if harbor_yaml_plan is None and config_supplied:
            self._prevalidate_config(
                config=dict(asset_config or {}),
                template=source_template,
                fallback_manifest=existing_manifest,
                worker_shards=worker_shards,
                all_case_ids=all_case_ids,
            )
```

In the rerun batch creation loop, create this dictionary before `rerun_batches`:

```python
            rerun_batch_case_ids: dict[str, list[str]] = {}
```

After each `batch = self.store.create_batch(...)`, add:

```python
                    rerun_batch_case_ids[str(batch["batch_id"])] = list(case_ids)
```

Replace the config application block:

```python
            if config_supplied:
                rerun_concurrency = self._apply_config(
                    run=derived_run,
                    config=dict(asset_config or {}),
                    fallback_manifest=existing_manifest,
                    worker_shards=worker_shards,
                    all_case_ids=all_case_ids,
                )
            else:
                self._set_derived_template_jobs_dir(run=derived_run, source_template=source_template)
```

with this block:

```python
            if harbor_yaml_plan is None and config_supplied:
                rerun_concurrency = self._apply_config(
                    run=derived_run,
                    config=dict(asset_config or {}),
                    fallback_manifest=existing_manifest,
                    worker_shards=worker_shards,
                    all_case_ids=all_case_ids,
                )
            elif harbor_yaml_plan is None:
                self._set_derived_template_jobs_dir(run=derived_run, source_template=source_template)
```

After rerun batches are created and before `self.store.update_run_rerun_job(...)`, add:

```python
            if harbor_yaml_plan is not None:
                self._apply_harbor_yaml_config(
                    run=derived_run,
                    plan=harbor_yaml_plan,
                    worker_shards=worker_shards,
                    rerun_batch_case_ids=rerun_batch_case_ids,
                )
```

- [ ] **Step 5: Run YAML-first confirm tests**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_ignores_submitted_task_names_and_writes_batch_yaml tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_rejects_changed_dataset_missing_selected_case tests/controller/test_run_rerun_coordinator.py::test_start_rerun_harbor_yaml_manifest_includes_bind_assets -q
```

Expected: PASS.

- [ ] **Step 6: Run coordinator regression tests**

Run:

```bash
uv run pytest tests/controller/test_run_rerun_coordinator.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add src/agent_eval_orchestrator/controller/run_rerun_coordinator.py tests/controller/test_run_rerun_coordinator.py
git commit -m "feat: apply Harbor YAML to exception reruns"
```

Expected: commit succeeds.

---

### Task 4: Sync Bind Assets for Rerun Jobs

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/asset_syncer.py`
- Test: `tests/controller/test_asset_syncer.py`

- [ ] **Step 1: Add failing rerun bind asset sync test**

Append this test to `tests/controller/test_asset_syncer.py`:

```python
def test_sync_rerun_job_syncs_bind_assets_and_records_asset_paths(store, tmp_path, sample_ssh_config):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    codeagent = tmp_path / "codeagentcli"
    codeagent.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(codeagent, 0o755)
    shared = tmp_path / "runtime"
    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(shared), "localToController": True},
    )
    template = store.create_task_template(
        owner="default",
        name="rerun-sync",
        dataset_ref=str(dataset),
        executor_kind="harbor-docker",
        executor_config={
            "harborYamlByBatchId": {"batch-rerun": "job_name: x\njobs_dir: jobs\n"},
            "uvBinaryByWorker": {"local-a": "/usr/bin/uv"},
        },
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="local-a",
        batch_options={},
        initial_status="pending_sync",
        batch_kind="exception_rerun",
    )
    job = store.create_run_rerun_job(
        job_id="rerun-job",
        run_id=run["run_id"],
        case_ids=["case-a"],
        worker_shards={"local-a": ["case-a"]},
        rerun_batches={"local-a": batch["batch_id"]},
        selected_error_types=["stderr"],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="syncing", rerun_job_id=job["job_id"])
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_manifest={
            "datasetPath": str(dataset),
            "bindAssets": [
                {"source": str(codeagent), "kind": "file", "targetName": "codeagentcli"},
            ],
            "workers": {
                "local-a": {
                    "caseIds": ["case-a"],
                    "targetRoot": str(shared / "sync" / run["run_id"]),
                    "transport": "local",
                }
            },
        },
    )
    syncer = AssetSyncer(store=store, ssh_config_path=sample_ssh_config, controller_shared_root=tmp_path)

    syncer.sync_rerun_job(job_id=job["job_id"], run_id=run["run_id"])

    updated_job = store.get_run_rerun_job(job["job_id"])
    assert updated_job["status"] == "running"
    sync_job = store.get_asset_sync_job(updated_job["sync_job_id"])
    assert sync_job["status"] == "succeeded"
    assert [step["id"] for step in sync_job["steps"][0]["steps"]] == ["sync_cases", "sync_assets"]
    copied_codeagent = shared / "sync" / run["run_id"] / "assets" / "codeagentcli"
    assert copied_codeagent.read_text(encoding="utf-8") == "#!/bin/sh\n"
    updated_template = store.get_task_template(template["template_id"])
    asset_paths = updated_template["executor_config"]["assetPathsByWorker"]["local-a"]
    assert asset_paths[str(codeagent)] == str(copied_codeagent)
```

- [ ] **Step 2: Run rerun bind asset test and verify failure**

Run:

```bash
uv run pytest tests/controller/test_asset_syncer.py::test_sync_rerun_job_syncs_bind_assets_and_records_asset_paths -q
```

Expected: FAIL because `sync_rerun_job()` does not create a `sync_assets` step or copy bind assets.

- [ ] **Step 3: Update `sync_rerun_job()` bind asset handling**

In `src/agent_eval_orchestrator/controller/asset_syncer.py`, replace this block in `sync_rerun_job()`:

```python
        include_bitfun = bool(str(manifest.get("bitfunCliPath") or "").strip()) and bool(
            str(manifest.get("bitfunConfigDir") or "").strip()
        )
        steps = initial_worker_steps(worker_ids, include_bitfun=include_bitfun)
```

with:

```python
        bind_assets = list(manifest.get("bindAssets") or [])
        include_bitfun = bool(str(manifest.get("bitfunCliPath") or "").strip()) and bool(
            str(manifest.get("bitfunConfigDir") or "").strip()
        )
        include_assets = bool(bind_assets) or include_bitfun
        steps = initial_worker_steps(worker_ids, include_assets=include_assets)
```

In the no-manifest fast path, replace references to `"sync_bitfun"` with `"sync_assets"`:

```python
                    if include_assets:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "failed")
```

and:

```python
                if include_assets:
                    steps = set_worker_step_status(steps, worker_id, "sync_assets", "succeeded")
```

Inside `worker_thread()`, replace the block after `_sync_cases()` that currently uses `"sync_bitfun"` with:

```python
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                    if include_assets:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "running")
                        self.store.update_asset_sync_job(sync_job_id, steps=steps)
                if include_assets:
                    if include_bitfun:
                        self._sync_bitfun(entry, manifest)
                    if bind_assets:
                        self._sync_bind_assets(entry, bind_assets)
                    with lock:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "succeeded")
                        self.store.update_asset_sync_job(sync_job_id, steps=steps)
```

Replace the executor config update in the same worker block with:

```python
                asset_paths = worker_asset_paths(
                    target_root=str(entry["targetRoot"]),
                    bind_assets=bind_assets,
                )
                patch = {
                    "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                    "mountsByWorker": {worker_id: paths["mounts"]},
                }
                if asset_paths:
                    patch["assetPathsByWorker"] = {worker_id: asset_paths}
                self.store.update_task_template_executor_config(
                    str(run["template_id"]),
                    patch,
                )
```

In the exception block, replace `"sync_bitfun"` with `"sync_assets"`:

```python
                    if include_assets:
                        steps = set_worker_step_status(steps, worker_id, "sync_assets", "failed")
```

- [ ] **Step 4: Run asset sync tests**

Run:

```bash
uv run pytest tests/controller/test_asset_syncer.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add src/agent_eval_orchestrator/controller/asset_syncer.py tests/controller/test_asset_syncer.py
git commit -m "feat: sync bind assets for rerun jobs"
```

Expected: commit succeeds.

---

### Task 5: Add Preview API and YAML-First Confirm API Coverage

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Test: `tests/controller/test_rerun_exceptions_api.py`

- [ ] **Step 1: Add failing API preview and confirm tests**

Append these tests to `tests/controller/test_rerun_exceptions_api.py`:

```python
def test_rerun_exceptions_harbor_yaml_preview_api_returns_original_yaml(store, tmp_path):
    dataset = tmp_path / "dataset"
    for case_id in ("exc-a", "ok"):
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    yaml_text = f"""
agents:
  - name: codex
datasets:
  - path: {dataset}
    task_names:
      - ok
"""
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    store.update_task_template_executor_config(run["template_id"], {"harborYaml": yaml_text})
    server = start_test_server(store, tmp_path, 9897)
    conn = HTTPConnection("127.0.0.1", 9897)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions/harbor-yaml-preview",
        body=json.dumps({"selectedErrorTypes": ["stderr"]}),
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 200
    assert payload["source"] == "original_yaml"
    assert payload["harborYaml"].strip() == yaml_text.strip()
    assert payload["exceptionCount"] == 1
    assert payload["workerShards"] == {"worker-a": 1}


def test_rerun_exceptions_api_accepts_harbor_yaml_body(store, tmp_path):
    dataset = tmp_path / "dataset"
    for case_id in ("exc-a", "ok"):
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    server = start_test_server(store, tmp_path, 9898)
    conn = HTTPConnection("127.0.0.1", 9898)
    harbor_yaml = f"""
job_name: ignored
n_concurrent_trials: 3
agents:
  - name: codex
datasets:
  - path: {dataset}
    task_names:
      - ok
"""
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body=json.dumps({"selectedErrorTypes": ["stderr"], "harborYaml": harbor_yaml}),
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 201
    assert payload["parentRunId"] == run["run_id"]
    assert payload["exceptionCount"] == 1
    derived_run = store.get_run(payload["runId"])
    derived_template = store.get_task_template(derived_run["template_id"])
    assert "harborYamlByBatchId" in derived_template["executor_config"]
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```bash
uv run pytest tests/controller/test_rerun_exceptions_api.py::test_rerun_exceptions_harbor_yaml_preview_api_returns_original_yaml tests/controller/test_rerun_exceptions_api.py::test_rerun_exceptions_api_accepts_harbor_yaml_body -q
```

Expected: first test FAILS with 404 because the preview route does not exist.

- [ ] **Step 3: Add preview route in `server.py`**

In `src/agent_eval_orchestrator/controller/server.py`, add this block before the existing `/api/runs/{runId}/rerun-exceptions` block:

```python
        if path.startswith("/api/runs/") and path.endswith("/rerun-exceptions/harbor-yaml-preview"):
            if self.run_rerun_coordinator is None:
                _json_response(self, {"error": "rerun coordinator unavailable"}, 500)
                return
            if not isinstance(body, dict):
                _json_response(self, {"error": "request body must be a JSON object"}, 400)
                return
            run_id = path.split("/")[3]
            try:
                result = self.run_rerun_coordinator.preview_harbor_yaml(run_id, config=body)
            except RerunValidationError as exc:
                _json_response(self, {"error": exc.message}, exc.code)
                return
            _json_response(self, result)
            return
```

The existing confirm route can remain:

```python
                result = self.run_rerun_coordinator.start_rerun(run_id, config=body)
```

- [ ] **Step 4: Run API tests**

Run:

```bash
uv run pytest tests/controller/test_rerun_exceptions_api.py::test_rerun_exceptions_harbor_yaml_preview_api_returns_original_yaml tests/controller/test_rerun_exceptions_api.py::test_rerun_exceptions_api_accepts_harbor_yaml_body -q
```

Expected: PASS.

- [ ] **Step 5: Run full rerun API tests**

Run:

```bash
uv run pytest tests/controller/test_rerun_exceptions_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_rerun_exceptions_api.py
git commit -m "feat: expose rerun Harbor YAML API"
```

Expected: commit succeeds.

---

### Task 6: Replace Rerun Modal Fields With Harbor YAML Editor

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`
- Test: `tests/controller/test_static_auth_token.py`

- [ ] **Step 1: Add failing static UI assertions**

Append this test to `tests/controller/test_static_auth_token.py`:

```python
def test_rerun_modal_uses_harbor_yaml_preview_and_payload() -> None:
    assert "rerunYamlPreview" in INDEX_HTML
    assert "loadRerunHarborYamlPreview" in INDEX_HTML
    assert 'name="rerunHarborYaml"' in INDEX_HTML
    assert 'harborYaml: String(data.get("rerunHarborYaml") || "").trim()' in INDEX_HTML
    assert '"/rerun-exceptions/harbor-yaml-preview"' in INDEX_HTML
    assert "Per Worker Concurrency" not in INDEX_HTML
    assert "BitFun CLI Path" not in INDEX_HTML
    assert "BitFun Config Root" not in INDEX_HTML
```

- [ ] **Step 2: Run static UI test and verify failure**

Run:

```bash
uv run pytest tests/controller/test_static_auth_token.py::test_rerun_modal_uses_harbor_yaml_preview_and_payload -q
```

Expected: FAIL because the rerun modal still renders old config fields.

- [ ] **Step 3: Update rerun config state**

In `src/agent_eval_orchestrator/controller/static.py`, replace `openRerunConfigModal(detail)` with:

```javascript
    async function openRerunConfigModal(detail) {
      const byType = (detail.exceptionSummary && detail.exceptionSummary.byType) || [];
      const defaultSelected = byType.map(entry => entry.errorType);
      state.rerunConfig = {
        runId: detail.run.run_id,
        detail,
        selectedErrorTypes: defaultSelected,
        yamlText: "",
        yamlSource: "",
        yamlDirty: false,
        preview: null,
        error: "",
        loadingPreview: false,
        submitting: false,
      };
      renderRerunConfigModal();
      document.getElementById("rerunConfigModal").classList.remove("hidden");
      await loadRerunHarborYamlPreview();
    }
```

- [ ] **Step 4: Add preview loader**

Add this function after `rerunSelectedCaseCount()`:

```javascript
    async function loadRerunHarborYamlPreview() {
      const modalState = state.rerunConfig;
      if (!modalState || modalState.loadingPreview) return;
      modalState.loadingPreview = true;
      modalState.error = "";
      renderRerunConfigModal();
      try {
        const result = await api("/api/runs/" + encodeURIComponent(modalState.runId) + "/rerun-exceptions/harbor-yaml-preview", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({selectedErrorTypes: modalState.selectedErrorTypes || []}),
        });
        modalState.preview = result;
        modalState.yamlSource = result.source || "";
        if (!modalState.yamlDirty) {
          modalState.yamlText = String(result.harborYaml || "");
        }
      } catch (error) {
        modalState.error = formatApiError(error);
      } finally {
        if (state.rerunConfig) {
          state.rerunConfig.loadingPreview = false;
          renderRerunConfigModal();
        }
      }
    }
```

- [ ] **Step 5: Replace form HTML in `renderRerunConfigModal()`**

In `renderRerunConfigModal()`, remove the three old `detail-grid` form sections containing `executorKind`, `agentName`, `nConcurrent`, timeout fields, dataset path, BitFun fields, and jobs dir. Replace the form string with:

```javascript
        '<form id="rerunConfigForm">' +
          '<div class="field" style="margin-bottom:16px">' +
            '<label>Harbor YAML</label>' +
            '<textarea name="rerunHarborYaml" required style="min-height:360px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace">' + esc(modalState.yamlText || "") + '</textarea>' +
            '<div class="subtle" style="margin-top:8px">Exception 类型决定重跑范围；YAML 中的 task_names/tasks 仅作为参数模板，最终执行范围由后台覆盖。</div>' +
            (modalState.loadingPreview ? '<div class="subtle" style="margin-top:8px">正在加载 YAML 预览…</div>' : '') +
            (modalState.yamlSource ? '<div class="subtle" style="margin-top:8px">YAML 来源：' + esc(modalState.yamlSource) + '</div>' : '') +
          '</div>' +
          '<div class="actions">' +
            '<button class="primary" type="submit"' + (submitDisabled || modalState.loadingPreview ? ' disabled' : '') + '>' + submitLabel + '</button>' +
            '<button class="ghost" type="button" id="cancelRerunConfigBtn"' + (modalState.submitting ? ' disabled' : '') + '>取消</button>' +
          '</div>' +
        '</form>';
```

After attaching the submit listener, add:

```javascript
      const yamlEditor = body.querySelector('textarea[name="rerunHarborYaml"]');
      if (yamlEditor) {
        yamlEditor.addEventListener("input", () => {
          modalState.yamlText = yamlEditor.value;
          modalState.yamlDirty = true;
        });
      }
```

- [ ] **Step 6: Refresh preview stats on type selection changes**

In the checkbox change handler, after updating `modalState.selectedErrorTypes`, replace `renderRerunConfigModal();` with:

```javascript
          renderRerunConfigModal();
          loadRerunHarborYamlPreview();
```

In the select-all and clear-all handlers, after updating `selectedErrorTypes`, replace `renderRerunConfigModal();` with:

```javascript
          renderRerunConfigModal();
          loadRerunHarborYamlPreview();
```

- [ ] **Step 7: Replace submit payload**

In `submitRerunConfigForm(event)`, remove the `collectTaskConfigPayload()` call and the `modalState.defaults = ...` block. Replace them with:

```javascript
      const data = new FormData(event.target);
      const harborYaml = String(data.get("rerunHarborYaml") || "").trim();
      if (!harborYaml) {
        modalState.error = "请填写 Harbor YAML";
        renderRerunConfigModal();
        return;
      }
      modalState.yamlText = harborYaml;
      modalState.yamlDirty = true;
```

Then replace the POST body with:

```javascript
          body: JSON.stringify({
            harborYaml,
            selectedErrorTypes: modalState.selectedErrorTypes || [],
          }),
```

- [ ] **Step 8: Run static UI test**

Run:

```bash
uv run pytest tests/controller/test_static_auth_token.py::test_rerun_modal_uses_harbor_yaml_preview_and_payload -q
```

Expected: PASS.

- [ ] **Step 9: Run all static UI tests**

Run:

```bash
uv run pytest tests/controller/test_static_auth_token.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 6**

Run:

```bash
git add src/agent_eval_orchestrator/controller/static.py tests/controller/test_static_auth_token.py
git commit -m "feat: use Harbor YAML in rerun modal"
```

Expected: commit succeeds.

---

### Task 7: Executor and Integration Regression

**Files:**
- Test: `tests/executors/test_harbor_executor.py`
- Test: `tests/controller/test_run_rerun_coordinator.py`
- Test: `tests/controller/test_rerun_exceptions_api.py`
- Test: `tests/controller/test_asset_syncer.py`
- Test: `tests/controller/test_static_auth_token.py`

- [ ] **Step 1: Run YAML-first executor regression**

Run:

```bash
uv run pytest tests/executors/test_harbor_executor.py::test_prepare_yaml_first_writes_config_and_uses_harbor_config_flag -q
```

Expected: PASS. This verifies rerun-generated `harborYamlByBatchId` continues to use `harbor run -c`.

- [ ] **Step 2: Run focused feature suite**

Run:

```bash
uv run pytest \
  tests/controller/test_harbor_yaml.py \
  tests/controller/test_run_rerun_coordinator.py \
  tests/controller/test_asset_syncer.py \
  tests/controller/test_rerun_exceptions_api.py \
  tests/controller/test_static_auth_token.py \
  tests/executors/test_harbor_executor.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run full controller and executor test suites**

Run:

```bash
uv run pytest tests/controller tests/executors -q
```

Expected: PASS.

- [ ] **Step 4: Inspect working tree**

Run:

```bash
git status --short
```

Expected: only the files changed by this feature are listed. Existing untracked `frontend/` and `runtime-v2/` may still appear and should not be staged unless the user explicitly asks.

- [ ] **Step 5: Commit final verification adjustments if any tracked files changed**

If Step 4 shows tracked files modified by this feature, run:

```bash
git add src/agent_eval_orchestrator/controller/harbor_yaml.py \
  src/agent_eval_orchestrator/controller/run_rerun_coordinator.py \
  src/agent_eval_orchestrator/controller/asset_syncer.py \
  src/agent_eval_orchestrator/controller/server.py \
  src/agent_eval_orchestrator/controller/static.py \
  tests/controller/test_harbor_yaml.py \
  tests/controller/test_run_rerun_coordinator.py \
  tests/controller/test_asset_syncer.py \
  tests/controller/test_rerun_exceptions_api.py \
  tests/controller/test_static_auth_token.py \
  tests/executors/test_harbor_executor.py
git commit -m "test: verify YAML-first exception rerun"
```

Expected: commit succeeds when there are tracked changes. If there are no tracked changes, skip this commit.

---

## Final Verification

Run:

```bash
uv run pytest -q
```

Expected: PASS.

If the full suite is too slow for the current execution window, run this minimum verification and report that the full suite was not run:

```bash
uv run pytest \
  tests/controller/test_harbor_yaml.py \
  tests/controller/test_run_rerun_coordinator.py \
  tests/controller/test_asset_syncer.py \
  tests/controller/test_rerun_exceptions_api.py \
  tests/controller/test_static_auth_token.py \
  tests/executors/test_harbor_executor.py \
  -q
```

Expected: PASS.

## Self-Review Checklist

- Spec coverage:
  - Preview endpoint: Task 2 and Task 5.
  - YAML-first confirm payload: Task 3 and Task 5.
  - YAML task range ignored for scope: Task 1 and Task 3.
  - Legacy YAML generation: Task 2.
  - Bind asset sync in reruns: Task 3 and Task 4.
  - Rerun modal YAML editor: Task 6.
  - Executor remains YAML-first: Task 7.

- Type consistency:
  - `parse_rerun_harbor_yaml(raw_yaml, selected_task_ids, timestamp)` returns `HarborYamlPlan`.
  - `RunRerunCoordinator.preview_harbor_yaml(run_id, config)` returns a JSON-serializable dict.
  - `RerunScope.worker_shards` remains `dict[str, list[str]]`.
  - `harborYamlByBatchId` remains keyed by batch id.

- Scope constraints:
  - Original run and original template are not modified.
  - Rerun case scope comes from selected exception types only.
  - Submitted YAML controls runtime parameters but not case scope.
