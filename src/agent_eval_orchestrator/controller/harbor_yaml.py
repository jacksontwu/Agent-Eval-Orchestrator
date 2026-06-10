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
