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
