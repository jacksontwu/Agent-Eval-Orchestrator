from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_eval_orchestrator.storage.store import Store


def derived_jobs_dir_for_run(*, store: "Store", run: dict[str, Any]) -> Path:
    return store.layout.run_dir(str(run["owner"]), str(run["run_id"])) / "harbor" / "jobs"


def copy_jobs_tree(source: Path, target: Path) -> None:
    if not source.exists() or not source.is_dir():
        raise RuntimeError(f"source jobs directory not found: {source}")
    resolved_source = source.resolve()
    resolved_target = target.resolve()
    if resolved_source == resolved_target:
        raise RuntimeError(f"source and target jobs directories must differ: {source}")
    try:
        resolved_source.relative_to(resolved_target)
        overlaps = True
    except ValueError:
        try:
            resolved_target.relative_to(resolved_source)
            overlaps = True
        except ValueError:
            overlaps = False
    if overlaps:
        raise RuntimeError(
            f"source and target jobs directories must not overlap: {source} -> {target}"
        )
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError(f"target jobs path exists but is not a directory: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _trial_case_id(trial_dir: Path) -> str:
    result_path = trial_dir / "result.json"
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        task_name = str(payload.get("task_name") or "").strip()
        if task_name:
            return task_name
        trial_name = str(payload.get("trial_name") or trial_dir.name).strip()
    else:
        trial_name = trial_dir.name
    if "__" not in trial_name:
        return trial_name
    candidate = trial_name.rsplit("__", 1)[0].strip()
    return candidate or trial_name


def _iter_trial_dirs(jobs_dir: Path) -> list[Path]:
    if not jobs_dir.exists():
        return []
    return sorted(
        child
        for job_dir in jobs_dir.iterdir()
        if job_dir.is_dir()
        for child in job_dir.iterdir()
        if child.is_dir() and (child / "result.json").exists()
    )


def delete_trials_for_cases(*, jobs_dir: Path, case_ids: list[str]) -> list[Path]:
    selected = {str(case_id) for case_id in case_ids}
    removed: list[Path] = []
    for trial_dir in _iter_trial_dirs(jobs_dir):
        if _trial_case_id(trial_dir) not in selected:
            continue
        removed.append(trial_dir)
        shutil.rmtree(trial_dir)
    return removed
