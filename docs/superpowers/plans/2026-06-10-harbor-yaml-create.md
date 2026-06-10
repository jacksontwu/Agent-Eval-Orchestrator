# Harbor YAML Create Task Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Create Task parameter grid with a Harbor YAML-first flow that distributes YAML task sets across workers while passing Harbor parameters through unchanged.

**Architecture:** Add a focused `controller/harbor_yaml.py` module that parses, validates, names, and shards Harbor YAML. The existing create API gets a YAML-first branch that creates queued batches directly and stores per-batch YAML in `executor_config.harborYamlByBatchId`; `HarborExecutor.prepare()` detects that shape and runs `uv run harbor run -c <generated-yaml> -y`. The legacy create and executor paths remain for existing tasks and rerun compatibility.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`/`pathlib`/`copy`, PyYAML safe loader/dumper, existing `Store`, `HarborExecutor`, controller static HTML/JS, pytest.

---

## Scope Check

The approved spec covers one subsystem: YAML-first task creation. The work touches UI, API, YAML planning, and worker execution, but all changes serve one end-to-end flow and are testable together. Exception rerun migration is explicitly out of scope.

## File Structure

- Create `src/agent_eval_orchestrator/controller/harbor_yaml.py`
  - Owns YAML parsing, validation, task enumeration, generated name construction, and per-batch YAML rendering.
  - Exposes `HarborYamlError`, `HarborYamlPlan`, `parse_harbor_yaml()`, and `build_batch_harbor_yaml()`.
- Create `tests/controller/test_harbor_yaml.py`
  - Fast unit tests for YAML contract and splitting behavior without HTTP or worker setup.
- Modify `pyproject.toml` and `uv.lock`
  - Add PyYAML as a runtime dependency.
- Modify `src/agent_eval_orchestrator/controller/server.py`
  - Adds a YAML-first branch for `POST /api/eval-tasks/create-and-distribute`.
  - Leaves the legacy branch intact.
- Modify `tests/controller/test_create_task_sync_api.py`
  - Adds create API coverage for YAML-first requests and validation errors.
- Modify `src/agent_eval_orchestrator/executors/harbor.py`
  - Adds an early YAML-first prepare branch.
  - Leaves the existing flag-building branch untouched.
- Modify `tests/executors/test_harbor_executor.py`
  - Adds executor coverage for config-file execution and absence of legacy flags.
- Modify `src/agent_eval_orchestrator/controller/static.py`
  - Replaces the Create form field set with one Harbor YAML textarea and updates payload collection/toast text.
- Modify `tests/controller/test_static_auth_token.py`
  - Updates static UI assertions to the YAML-first form.

---

### Task 1: Add PyYAML Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add the dependency with uv**

Run:

```bash
uv add "PyYAML>=6.0"
```

Expected: `pyproject.toml` gains a `PyYAML>=6.0` dependency and `uv.lock` is updated.

- [ ] **Step 2: Verify import works in the project environment**

Run:

```bash
uv run python -c "import yaml; print(yaml.__name__)"
```

Expected:

```text
yaml
```

- [ ] **Step 3: Commit dependency**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add yaml parser dependency"
```

---

### Task 2: Build Harbor YAML Planner

**Files:**
- Create: `src/agent_eval_orchestrator/controller/harbor_yaml.py`
- Create: `tests/controller/test_harbor_yaml.py`

- [ ] **Step 1: Write failing YAML planner tests**

Create `tests/controller/test_harbor_yaml.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlError,
    build_batch_harbor_yaml,
    parse_harbor_yaml,
)


def _task(root: Path, name: str) -> None:
    path = root / name
    path.mkdir(parents=True)
    (path / "task.toml").write_text("", encoding="utf-8")


def test_parse_datasets_task_names_and_build_batch_yaml(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    _task(dataset, "alpha")
    _task(dataset, "beta")
    raw = f"""
job_name: user-job
jobs_dir: user-jobs
timeout_multiplier: 1.0
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - alpha
      - beta
    n_tasks: 99
environment:
  type: docker
"""

    plan = parse_harbor_yaml(raw, timestamp="20260610-120000")

    assert plan.mode == "datasets"
    assert plan.task_ids == ["alpha", "beta"]
    assert plan.dataset_ref == str(dataset)
    assert plan.generated_job_name == "codex-openai-gpt-4o-tasks-20260610-120000"

    batch_yaml = build_batch_harbor_yaml(
        plan,
        batch_id="batch-a",
        selected_task_ids=["beta"],
        jobs_dir="/tmp/batch/harbor/jobs",
    )
    payload = yaml.safe_load(batch_yaml)
    assert payload["job_name"] == plan.generated_job_name
    assert payload["jobs_dir"] == "/tmp/batch/harbor/jobs"
    assert payload["timeout_multiplier"] == 1.0
    assert payload["environment"] == {"type": "docker"}
    assert payload["datasets"][0]["path"] == str(dataset)
    assert payload["datasets"][0]["task_names"] == ["beta"]
    assert "n_tasks" not in payload["datasets"][0]


def test_parse_datasets_enumerates_and_applies_n_tasks_globally(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    _task(dataset, "alpha")
    _task(dataset, "beta")
    _task(dataset, "gamma")
    raw = f"""
agents:
  - name: agent
    model_name: model
datasets:
  - path: {dataset}
    n_tasks: 2
"""

    plan = parse_harbor_yaml(raw, timestamp="20260610-120000")

    assert plan.task_ids == ["alpha", "beta"]


def test_parse_tasks_mode_preserves_selected_task_objects(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    _task(dataset, "alpha")
    _task(dataset, "beta")
    raw = f"""
agents:
  - name: codex
    model_name: openai/gpt-4o
tasks:
  - path: {dataset / "alpha"}
    metadata:
      split: one
  - path: {dataset / "beta"}
    metadata:
      split: two
"""

    plan = parse_harbor_yaml(raw, timestamp="20260610-120000")
    batch_yaml = build_batch_harbor_yaml(
        plan,
        batch_id="batch-a",
        selected_task_ids=["beta"],
        jobs_dir="/tmp/batch/harbor/jobs",
    )
    payload = yaml.safe_load(batch_yaml)

    assert plan.mode == "tasks"
    assert plan.dataset_ref == str(dataset / "alpha")
    assert plan.task_ids == ["alpha", "beta"]
    assert payload["tasks"] == [{"path": str(dataset / "beta"), "metadata": {"split": "two"}}]


def test_rejects_both_datasets_and_tasks(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    _task(dataset, "alpha")
    raw = f"""
datasets:
  - path: {dataset}
tasks:
  - path: {dataset / "alpha"}
"""

    with pytest.raises(HarborYamlError, match="exactly one of datasets or tasks"):
        parse_harbor_yaml(raw, timestamp="20260610-120000")


def test_rejects_missing_task_names(tmp_path: Path) -> None:
    dataset = tmp_path / "tasks"
    _task(dataset, "alpha")
    raw = f"""
datasets:
  - path: {dataset}
    task_names:
      - alpha
      - missing
"""

    with pytest.raises(HarborYamlError, match="missing task_names"):
        parse_harbor_yaml(raw, timestamp="20260610-120000")


def test_rejects_invalid_yaml() -> None:
    with pytest.raises(HarborYamlError, match="valid Harbor YAML"):
        parse_harbor_yaml("datasets: [", timestamp="20260610-120000")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev pytest tests/controller/test_harbor_yaml.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_eval_orchestrator.controller.harbor_yaml'`.

- [ ] **Step 3: Create YAML planner implementation**

Create `src/agent_eval_orchestrator/controller/harbor_yaml.py`:

```python
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_eval_orchestrator.core.ids import safe_timestamp, sanitize_name


class HarborYamlError(ValueError):
    """Raised when submitted Harbor YAML cannot be distributed by AEO."""


@dataclass(frozen=True)
class HarborYamlPlan:
    original_yaml: str
    original_config: dict[str, Any]
    mode: str
    dataset_ref: str
    task_ids: list[str]
    generated_job_name: str
    timestamp: str
    tasks_by_id: dict[str, dict[str, Any]]


def parse_harbor_yaml(raw_yaml: str, *, timestamp: str | None = None) -> HarborYamlPlan:
    raw_yaml = str(raw_yaml or "").strip()
    if not raw_yaml:
        raise HarborYamlError("harborYaml is required; paste valid Harbor YAML")
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
        dataset_ref, task_ids, tasks_by_id = _resolve_dataset_tasks(loaded)
        mode = "datasets"
    else:
        dataset_ref, task_ids, tasks_by_id = _resolve_direct_tasks(loaded)
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


def build_batch_harbor_yaml(
    plan: HarborYamlPlan,
    *,
    batch_id: str,
    selected_task_ids: list[str],
    jobs_dir: str,
) -> str:
    if not selected_task_ids:
        raise HarborYamlError(f"batch {batch_id} has no selected task ids")
    missing = [task_id for task_id in selected_task_ids if task_id not in plan.task_ids]
    if missing:
        raise HarborYamlError(f"batch {batch_id} references unknown task ids: {', '.join(missing[:5])}")

    payload = deepcopy(plan.original_config)
    payload["job_name"] = plan.generated_job_name
    payload["jobs_dir"] = str(jobs_dir)
    if plan.mode == "datasets":
        dataset_entry = dict(payload["datasets"][0])
        dataset_entry["task_names"] = list(selected_task_ids)
        dataset_entry.pop("n_tasks", None)
        payload["datasets"] = [dataset_entry]
    else:
        payload["tasks"] = [deepcopy(plan.tasks_by_id[task_id]) for task_id in selected_task_ids]
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _resolve_dataset_tasks(config: dict[str, Any]) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
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

    raw_task_names = dataset.get("task_names")
    if raw_task_names is None:
        task_ids = _enumerate_dataset_tasks(dataset_path)
    elif isinstance(raw_task_names, list):
        task_ids = [str(item).strip() for item in raw_task_names if str(item).strip()]
        if not task_ids:
            raise HarborYamlError("datasets[0].task_names must not be empty")
    else:
        raise HarborYamlError("datasets[0].task_names must be a list")

    missing = [task_id for task_id in task_ids if not (dataset_path / task_id).exists()]
    if missing:
        raise HarborYamlError("missing task_names under dataset path: " + ", ".join(missing[:5]))

    n_tasks = dataset.get("n_tasks")
    if n_tasks not in (None, ""):
        limit = int(n_tasks)
        if limit < 1:
            raise HarborYamlError("datasets[0].n_tasks must be at least 1")
        task_ids = task_ids[:limit]

    tasks_by_id = {task_id: {"path": str(dataset_path / task_id)} for task_id in task_ids}
    return str(dataset_path), task_ids, tasks_by_id


def _resolve_direct_tasks(config: dict[str, Any]) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    tasks = config.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise HarborYamlError("tasks must be a non-empty list")
    task_ids: list[str] = []
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            raise HarborYamlError(f"tasks[{index}] must be a mapping")
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.exists():
            raise HarborYamlError(f"task path not found: {path}")
        task_id = path.name
        if task_id in tasks_by_id:
            raise HarborYamlError(f"duplicate task basename in tasks: {task_id}")
        copied = deepcopy(item)
        copied["path"] = str(path)
        task_ids.append(task_id)
        tasks_by_id[task_id] = copied
    return str(Path(tasks_by_id[task_ids[0]]["path"])), task_ids, tasks_by_id


def _enumerate_dataset_tasks(dataset_path: Path) -> list[str]:
    with_task_toml = sorted(
        item.name for item in dataset_path.iterdir() if item.is_dir() and (item / "task.toml").exists()
    )
    if with_task_toml:
        return with_task_toml
    dirs = sorted(item.name for item in dataset_path.iterdir() if item.is_dir())
    if not dirs:
        raise HarborYamlError(f"dataset has no tasks: {dataset_path}")
    return dirs


def _generated_job_name(config: dict[str, Any], *, dataset_ref: str, timestamp: str) -> str:
    agent = "agent"
    model = "model"
    agents = config.get("agents")
    if isinstance(agents, list) and agents and isinstance(agents[0], dict):
        first_agent = agents[0]
        agent = str(first_agent.get("name") or agent)
        model_info = first_agent.get("model_info") if isinstance(first_agent.get("model_info"), dict) else {}
        model = str(first_agent.get("model_name") or model_info.get("name") or first_agent.get("model") or model)
    dataset_name = Path(dataset_ref).name or "task"
    return sanitize_name(f"{agent}-{model}-{dataset_name}-{timestamp}")[:120]
```

- [ ] **Step 4: Run YAML planner tests**

Run:

```bash
uv run --extra dev pytest tests/controller/test_harbor_yaml.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit YAML planner**

```bash
git add src/agent_eval_orchestrator/controller/harbor_yaml.py tests/controller/test_harbor_yaml.py
git commit -m "feat: add harbor yaml planner"
```

---

### Task 3: Add YAML-First Create API Branch

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/server.py`
- Modify: `tests/controller/test_create_task_sync_api.py`

- [ ] **Step 1: Add failing create API tests**

Append to `tests/controller/test_create_task_sync_api.py`:

```python
def test_create_task_yaml_first_creates_queued_batches_without_sync(store, tmp_path):
    dataset = tmp_path / "tasks"
    for name in ("alpha", "beta", "gamma"):
        case_dir = dataset / name
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    store.register_worker(
        worker_id="local-a",
        display_name="local-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-a"), "localToController": True},
    )
    store.register_worker(
        worker_id="local-b",
        display_name="local-b",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime-b"), "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9884)
    conn = HTTPConnection("127.0.0.1", 9884)
    body = json.dumps(
        {
            "harborYaml": f"""
job_name: user-job
jobs_dir: user-jobs
n_concurrent_trials: 4
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - alpha
      - beta
      - gamma
""",
            "workerIds": ["local-a", "local-b"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 201
    assert payload["syncJobId"] is None
    assert payload["run"]["syncStatus"] is None
    assert payload["run"]["display_name"].startswith("codex-openai-gpt-4o-tasks-")
    assert {batch["status"] for batch in payload["batches"]} == {"queued"}
    assert len(payload["batches"]) == 2
    config = payload["template"]["executor_config"]
    assert config["harborYamlMode"] == "datasets"
    assert sorted(config["harborYamlByBatchId"]) == sorted(batch["batch_id"] for batch in payload["batches"])
    assert "bitfunCliPath" not in config


def test_create_task_yaml_first_rejects_invalid_worker(store, tmp_path):
    dataset = tmp_path / "tasks"
    case_dir = dataset / "alpha"
    case_dir.mkdir(parents=True)
    (case_dir / "task.toml").write_text("", encoding="utf-8")
    server = start_test_server(store, tmp_path, 9885)
    conn = HTTPConnection("127.0.0.1", 9885)
    body = json.dumps(
        {
            "harborYaml": f"""
datasets:
  - path: {dataset}
    task_names:
      - alpha
""",
            "workerIds": ["missing-worker"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    server.shutdown()

    assert resp.status == 400
    assert "worker not found" in payload["error"]
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
uv run --extra dev pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_creates_queued_batches_without_sync tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rejects_invalid_worker -v
```

Expected: FAIL with 400 missing legacy fields or missing YAML-first branch.

- [ ] **Step 3: Add imports to `server.py`**

Modify the imports near the top of `src/agent_eval_orchestrator/controller/server.py`:

```python
from agent_eval_orchestrator.controller.harbor_yaml import (
    HarborYamlError,
    build_batch_harbor_yaml,
    parse_harbor_yaml,
)
```

- [ ] **Step 4: Route YAML-first requests before the legacy create logic**

Inside `Handler.do_POST()`, at the start of the `if path == "/api/eval-tasks/create-and-distribute":` block, insert:

```python
        if path == "/api/eval-tasks/create-and-distribute":
            if "harborYaml" in body:
                self._create_yaml_eval_task(body)
                return
            try:
                owner = DEFAULT_OWNER
```

This keeps the legacy branch starting at its existing `try:` block.

- [ ] **Step 5: Add the YAML-first helper method to `Handler`**

Add this method inside `Handler`, just before `do_POST()`:

```python
    def _create_yaml_eval_task(self, body: dict[str, Any]) -> None:
        try:
            worker_ids = [
                str(item).strip()
                for item in body.get("workerIds") or []
                if str(item).strip()
            ]
            if not worker_ids:
                raise HarborYamlError("workerIds must not be empty")
            workers = self.store.list_workers()
            workers_by_id = {str(item["worker_id"]): item for item in workers}
            missing_workers = [worker_id for worker_id in worker_ids if worker_id not in workers_by_id]
            if missing_workers:
                raise HarborYamlError("worker not found: " + ", ".join(missing_workers))

            plan = parse_harbor_yaml(str(body.get("harborYaml") or ""))
            template = self.store.create_task_template(
                owner=DEFAULT_OWNER,
                name=plan.generated_job_name,
                dataset_ref=plan.dataset_ref,
                executor_kind="harbor-docker",
                executor_config={
                    "harborYaml": plan.original_yaml,
                    "harborYamlMode": plan.mode,
                    "harborYamlTaskIds": plan.task_ids,
                    "harborYamlGeneratedJobName": plan.generated_job_name,
                    "harborYamlTimestamp": plan.timestamp,
                    "collectJobs": True,
                    "combinedJobsDir": "",
                },
                model_profile_ref=str(body.get("modelProfileRef") or "") or None,
                note="",
            )
            run = self.store.create_run(
                template_id=str(template["template_id"]),
                display_name=plan.generated_job_name,
            )
            batches = self.store.create_sharded_batches(
                run_id=str(run["run_id"]),
                selected_case_ids=plan.task_ids,
                worker_ids=worker_ids,
                batch_options={"concurrency": int((plan.original_config.get("n_concurrent_trials") or 1))},
                initial_status="queued",
            )
            yaml_by_batch_id = {}
            combined_jobs_dir = ""
            for batch in batches:
                jobs_dir = Path(str(batch["batch_root"])) / "harbor" / "jobs"
                if not combined_jobs_dir:
                    combined_jobs_dir = str(jobs_dir)
                yaml_by_batch_id[str(batch["batch_id"])] = build_batch_harbor_yaml(
                    plan,
                    batch_id=str(batch["batch_id"]),
                    selected_task_ids=[str(item) for item in batch.get("selected_case_ids") or []],
                    jobs_dir=str(jobs_dir),
                )
            template = self.store.update_task_template_executor_config(
                str(template["template_id"]),
                {
                    "harborYamlByBatchId": yaml_by_batch_id,
                    "combinedJobsDir": combined_jobs_dir,
                },
                replace_keys={"harborYamlByBatchId"},
            )
            run = self.store.get_run(str(run["run_id"])) or run
        except HarborYamlError as exc:
            _json_response(self, {"error": str(exc)}, 400)
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
                    "syncStatus": run.get("sync_status"),
                },
                "batches": batches,
                "syncJobId": None,
            },
            201,
        )
```

- [ ] **Step 6: Run YAML-first API tests**

Run:

```bash
uv run --extra dev pytest tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_creates_queued_batches_without_sync tests/controller/test_create_task_sync_api.py::test_create_task_yaml_first_rejects_invalid_worker -v
```

Expected: PASS.

- [ ] **Step 7: Run legacy create API tests**

Run:

```bash
uv run --extra dev pytest tests/controller/test_create_task_sync_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit API branch**

```bash
git add src/agent_eval_orchestrator/controller/server.py tests/controller/test_create_task_sync_api.py
git commit -m "feat: add yaml-first create api"
```

---

### Task 4: Add YAML-First Harbor Executor Branch

**Files:**
- Modify: `src/agent_eval_orchestrator/executors/harbor.py`
- Modify: `tests/executors/test_harbor_executor.py`

- [ ] **Step 1: Add failing executor test**

Append to `tests/executors/test_harbor_executor.py`:

```python
def test_prepare_yaml_first_writes_config_and_uses_harbor_config_flag(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch-root"
    batch_root.mkdir()
    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()
    dataset = tmp_path / "tasks"
    task = dataset / "alpha"
    task.mkdir(parents=True)
    (task / "task.toml").write_text("", encoding="utf-8")
    yaml_text = f"""
job_name: codex-openai-gpt-4o-tasks-20260610-120000
jobs_dir: {batch_root / "harbor" / "jobs"}
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - alpha
timeout_multiplier: 9.0
"""

    prepared = HarborExecutor().prepare(
        batch={
            "batch_id": "batch-test",
            "batch_root": str(batch_root),
            "selected_case_ids": ["alpha"],
        },
        run={},
        template={},
        dataset_ref=str(dataset),
        executor_config={
            "harborRepoPath": str(harbor_repo),
            "uvBinary": "/usr/bin/uv",
            "harborYamlGeneratedJobName": "codex-openai-gpt-4o-tasks-20260610-120000",
            "harborYamlByBatchId": {"batch-test": yaml_text},
            "agentName": "bitfun-cli",
            "modelName": "deepseek-v4-pro",
            "agentEnv": {"ANTHROPIC_API_KEY": "secret"},
            "timeoutMultiplier": 1.0,
        },
        local_root=tmp_path / "local",
        shared_root=None,
    )

    config_path = batch_root / "harbor-config.yaml"
    shell = prepared.command[2]
    assert config_path.exists()
    assert "harbor run -c" in shell
    assert str(config_path) in shell
    assert "-a bitfun-cli" not in shell
    assert "-m deepseek-v4-pro" not in shell
    assert "--ae ANTHROPIC_API_KEY=secret" not in shell
    assert "--timeout-multiplier 1.0" not in shell
    assert prepared.job_name == "codex-openai-gpt-4o-tasks-20260610-120000"
    assert prepared.jobs_dir == batch_root / "harbor" / "jobs"
    assert prepared.metadata["selectedCaseIds"] == ["alpha"]
```

- [ ] **Step 2: Run executor test to verify it fails**

Run:

```bash
uv run --extra dev pytest tests/executors/test_harbor_executor.py::test_prepare_yaml_first_writes_config_and_uses_harbor_config_flag -v
```

Expected: FAIL because `HarborExecutor.prepare()` still builds legacy flags.

- [ ] **Step 3: Add YAML-first helper methods to `HarborExecutor`**

In `src/agent_eval_orchestrator/executors/harbor.py`, add these methods inside `HarborExecutor` before `prepare()`:

```python
    def _prepare_from_harbor_yaml(
        self,
        *,
        batch: dict[str, Any],
        executor_config: dict[str, Any],
        local_root: Path,
        shared_root: Path | None,
    ) -> PreparedBatch:
        batch_id = str(batch["batch_id"])
        batch_root = Path(str(batch["batch_root"])).resolve()
        batch_root.mkdir(parents=True, exist_ok=True)
        yaml_by_batch = executor_config.get("harborYamlByBatchId")
        if not isinstance(yaml_by_batch, dict) or batch_id not in yaml_by_batch:
            raise RuntimeError(f"missing harborYamlByBatchId for batch: {batch_id}")
        harbor_yaml = str(yaml_by_batch[batch_id])
        config_path = batch_root / "harbor-config.yaml"
        config_path.write_text(harbor_yaml, encoding="utf-8")

        jobs_dir = batch_root / "harbor" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_name = str(executor_config.get("harborYamlGeneratedJobName") or batch_id)
        job_dir = jobs_dir / job_name
        worker_log_path = batch_root / "worker.log"
        worker_id = str(batch.get("assigned_worker_id") or batch.get("preferred_worker_id") or "").strip() or None
        harbor_root = resolve_harbor_repo(
            explicit=str(executor_config.get("harborRepoPath") or "").strip() or None,
            shared_root=shared_root,
            configured=self._worker_mapping_value(executor_config, worker_id, "harborRepoPath"),
            default=DEFAULT_HARBOR_REPO,
        )
        uv_binary = resolve_uv_binary(
            explicit=str(executor_config.get("uvBinary") or "").strip() or None,
            configured=self._worker_mapping_value(executor_config, worker_id, "uvBinary"),
            shared_root=shared_root,
        )
        harbor_args = ["run", "harbor", "run", "-c", str(config_path), "-y"]
        quoted_uv = shlex.quote(uv_binary)
        quoted_args = " ".join(shlex.quote(arg) for arg in harbor_args)
        command = [
            "/bin/bash",
            "-lc",
            (
                f"UV={quoted_uv}; "
                f'if ! command -v "$UV" >/dev/null 2>&1 && [ ! -x "$UV" ]; then '
                "curl -LsSf https://astral.sh/uv/install.sh | sh; "
                'UV="$(command -v uv || true)"; '
                "fi; "
                'if [ -z "$UV" ]; then echo "uv not found after install" >&2; exit 127; fi; '
                f'exec "$UV" {quoted_args}'
            ),
        ]
        selected_case_ids = list(batch.get("selected_case_ids") or [])
        metadata = {
            "executorKind": self.kind,
            "harborRepoPath": str(harbor_root),
            "jobName": job_name,
            "jobsDir": str(jobs_dir),
            "datasetPath": "",
            "selectedCaseIds": selected_case_ids,
            "command": command,
            "uvBinary": uv_binary,
            "collectJobs": True,
            "combinedJobsDir": str(executor_config.get("combinedJobsDir") or ""),
            "harborConfigPath": str(config_path),
        }
        return PreparedBatch(
            command=command,
            env={"PYTHONUNBUFFERED": "1"},
            cwd=harbor_root,
            batch_root=batch_root,
            local_root=local_root,
            job_name=job_name,
            jobs_dir=jobs_dir,
            job_dir=job_dir,
            dataset_path=config_path,
            worker_log_path=worker_log_path,
            metadata=metadata,
        )
```

- [ ] **Step 4: Call YAML-first branch at the start of `prepare()`**

At the top of `HarborExecutor.prepare()`, immediately after the function signature and before `batch_root = ...`, insert:

```python
        if "harborYamlByBatchId" in executor_config:
            return self._prepare_from_harbor_yaml(
                batch=batch,
                executor_config=executor_config,
                local_root=local_root,
                shared_root=shared_root,
            )
```

- [ ] **Step 5: Run executor YAML-first test**

Run:

```bash
uv run --extra dev pytest tests/executors/test_harbor_executor.py::test_prepare_yaml_first_writes_config_and_uses_harbor_config_flag -v
```

Expected: PASS.

- [ ] **Step 6: Run full executor tests**

Run:

```bash
uv run --extra dev pytest tests/executors/test_harbor_executor.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit executor branch**

```bash
git add src/agent_eval_orchestrator/executors/harbor.py tests/executors/test_harbor_executor.py
git commit -m "feat: run harbor yaml configs on workers"
```

---

### Task 5: Replace Create Page With YAML-First UI

**Files:**
- Modify: `src/agent_eval_orchestrator/controller/static.py`
- Modify: `tests/controller/test_static_auth_token.py`

- [ ] **Step 1: Replace static UI tests**

In `tests/controller/test_static_auth_token.py`, remove these legacy tests:

```python
def test_create_form_agent_name_input_is_editable() -> None:
    agent_input = create_form_inputs()["agentName"]

    assert "readonly" not in agent_input
    assert "disabled" not in agent_input


def test_create_form_exposes_model_agent_env_and_agent_kwargs() -> None:
    inputs = create_form_inputs()
    textareas = create_form_textareas()

    assert inputs["modelName"]["value"] == "deepseek-v4-pro"
    assert "agentEnv" in textareas
    assert "agentKwargs" in textareas


def test_create_payload_parses_agent_env_and_kwargs_into_executor_config() -> None:
    assert "function parseKeyValueLines" in INDEX_HTML
    assert 'const modelName = String(data.get("modelName") || "").trim()' in INDEX_HTML
    assert 'const agentEnv = parseKeyValueLines(data.get("agentEnv"))' in INDEX_HTML
    assert 'const agentKwargs = parseKeyValueLines(data.get("agentKwargs"))' in INDEX_HTML
    assert "if (modelName) executorConfig.modelName = modelName" in INDEX_HTML
    assert "if (Object.keys(agentEnv).length) executorConfig.agentEnv = agentEnv" in INDEX_HTML
    assert "if (Object.keys(agentKwargs).length) executorConfig.agentKwargs = agentKwargs" in INDEX_HTML
```

Add these YAML-first tests:

```python
def test_create_form_uses_harbor_yaml_textarea() -> None:
    inputs = create_form_inputs()
    textareas = create_form_textareas()

    assert "harborYaml" in textareas
    assert "name" not in inputs
    assert "agentName" not in inputs
    assert "modelName" not in inputs
    assert "bitfunCliPath" not in inputs
    assert "bitfunConfigDir" not in inputs
    assert "timeoutMultiplier" not in inputs
    assert "selectedCaseIds" not in textareas
    assert "agentEnv" not in textareas
    assert "agentKwargs" not in textareas


def test_create_payload_sends_harbor_yaml_and_worker_ids_only() -> None:
    assert 'harborYaml: String(data.get("harborYaml") || "").trim()' in INDEX_HTML
    assert "任务已创建，正在分发到 worker" in INDEX_HTML
    assert "任务已创建，正在同步资产到 worker" not in INDEX_HTML
```

- [ ] **Step 2: Run static tests to verify they fail**

Run:

```bash
uv run --extra dev pytest tests/controller/test_static_auth_token.py::test_create_form_uses_harbor_yaml_textarea tests/controller/test_static_auth_token.py::test_create_payload_sends_harbor_yaml_and_worker_ids_only -v
```

Expected: FAIL because the old form still contains legacy fields.

- [ ] **Step 3: Replace Create form markup**

In `src/agent_eval_orchestrator/controller/static.py`, replace the current `<section id="createView"...>` form fields with this form body:

```html
          <form id="createTaskForm">
            <div class="field" style="margin-bottom:16px">
              <label>Harbor YAML</label>
              <textarea name="harborYaml" required style="min-height:360px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"></textarea>
              <div class="subtle" style="margin-top:8px">粘贴 harbor run -c 使用的 YAML；AEO 只解析任务范围、分发 worker，并生成 job name，其它参数原样透传。</div>
            </div>

            <div style="margin-bottom:16px">
              <h3 style="margin-bottom:10px">Workers</h3>
              <div class="subtle" style="margin-bottom:10px">勾选参与执行的 worker；创建后 controller 会按 worker 容量拆分 YAML 任务集合。</div>
              <div id="createWorkerConfigs"></div>
            </div>

            <div class="actions">
              <button class="primary" type="submit">创建并分发任务</button>
            </div>
          </form>
```

Also change the panel subtitle to:

```html
<div class="subtle">粘贴 Harbor YAML，controller 自动切分任务并透传配置到 worker</div>
```

- [ ] **Step 4: Replace create payload collection**

In `src/agent_eval_orchestrator/controller/static.py`, keep `collectTaskConfigPayload(form)` because the exception rerun modal still uses it. Replace only `collectCreateFormPayload(form)` with:

```javascript
    function collectCreateFormPayload(form) {
      const data = new FormData(form);
      const workerIds = data.getAll("workerIds").map(value => String(value));
      return {
        harborYaml: String(data.get("harborYaml") || "").trim(),
        workerIds,
      };
    }
```

- [ ] **Step 5: Update submit validation and toast**

In `submitCreateTaskForm(event)`, after the worker check, add:

```javascript
      if (!payload.harborYaml) {
        alert("请粘贴 Harbor YAML。");
        return;
      }
```

Change:

```javascript
        showToast("任务已创建，正在同步资产到 worker");
```

to:

```javascript
        showToast("任务已创建，正在分发到 worker");
```

- [ ] **Step 6: Remove dataset prefill dependency from Create form**

In `applyCreateDefaults()`, replace the function body with:

```javascript
    function applyCreateDefaults() {
      renderCreateWorkers();
    }
```

This keeps worker rendering but stops trying to fill the removed `datasetPathInput`.

- [ ] **Step 7: Run static UI tests**

Run:

```bash
uv run --extra dev pytest tests/controller/test_static_auth_token.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit UI change**

```bash
git add src/agent_eval_orchestrator/controller/static.py tests/controller/test_static_auth_token.py
git commit -m "feat: simplify create task yaml form"
```

---

### Task 6: Run End-to-End Regression Suite

**Files:**
- No source edits expected.

- [ ] **Step 1: Run targeted changed-area tests**

Run:

```bash
uv run --extra dev pytest tests/controller/test_harbor_yaml.py tests/controller/test_create_task_sync_api.py tests/executors/test_harbor_executor.py tests/controller/test_static_auth_token.py -v
```

Expected: PASS.

- [ ] **Step 2: Run controller and storage regression tests**

Run:

```bash
uv run --extra dev pytest tests/controller tests/storage -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run --extra dev pytest -v
```

Expected: PASS.

- [ ] **Step 4: Inspect git status**

Run:

```bash
git status --short
```

Expected: only unrelated pre-existing untracked paths may remain. No modified tracked files should be unstaged.

- [ ] **Step 5: Manual browser verification**

Start or restart the controller:

```bash
scripts/aeo-controller.sh restart
```

Expected: logs include `controller started` and `listen: http://...:7380` if `.env` is configured. Open or refresh `http://111.119.196.110:7380/create`. Verify:

- The Create view shows one `Harbor YAML` textarea and worker checkboxes.
- The old agent/model/BitFun/timeout/selected-case fields are gone.
- Submitting a valid YAML with one worker creates a queued run.
- Submitting empty YAML shows `请粘贴 Harbor YAML。`.

- [ ] **Step 6: Commit verification note if code changed during fixes**

If Task 6 required fixes, commit them:

```bash
git add src tests pyproject.toml uv.lock
git commit -m "fix: stabilize harbor yaml create flow"
```

If Task 6 required no fixes, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- YAML textarea plus worker selection: Task 5.
- Harbor `datasets[0]` and `tasks[*]`: Task 2.
- `task_names`, enumeration, and global `n_tasks`: Task 2.
- Existing weighted worker split: Task 3 uses `create_sharded_batches()`.
- Generated name from agent/model/dataset/timestamp: Task 2 and Task 3.
- Mutate only `job_name`, `jobs_dir`, and task subset: Task 2.
- Worker `harbor run -c`: Task 4.
- Legacy compatibility: Tasks 3, 4, and 6 run legacy tests.

Type and name consistency:

- YAML storage key is `executor_config.harborYamlByBatchId`.
- Generated name key is `executor_config.harborYamlGeneratedJobName`.
- YAML mode key is `executor_config.harborYamlMode`.
- Helper functions are `parse_harbor_yaml()` and `build_batch_harbor_yaml()`.
