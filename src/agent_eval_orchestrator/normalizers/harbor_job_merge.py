from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from agent_eval_orchestrator.core.defaults import DEFAULT_HARBOR_REPO
from agent_eval_orchestrator.normalizers.harbor_timestamps import normalize_job_result_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FINALIZE_SCRIPT = _REPO_ROOT / "scripts" / "harbor_finalize_job_result.py"


def _iter_trial_dirs(job_dir: Path) -> list[Path]:
    if not job_dir.exists():
        return []
    return sorted(
        child
        for child in job_dir.iterdir()
        if child.is_dir() and (child / "result.json").exists()
    )


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


def _remove_matching_trial_dirs(target_job_dir: Path, case_id: str) -> None:
    for existing in _iter_trial_dirs(target_job_dir):
        if _trial_case_id(existing) == case_id:
            shutil.rmtree(existing)


def copy_trial_dirs(source_job_dir: Path, target_job_dir: Path) -> None:
    target_job_dir.mkdir(parents=True, exist_ok=True)
    for trial_dir in _iter_trial_dirs(source_job_dir):
        case_id = _trial_case_id(trial_dir)
        _remove_matching_trial_dirs(target_job_dir, case_id)
        shutil.copytree(trial_dir, target_job_dir / trial_dir.name)


def resolve_controller_harbor_repo() -> Path:
    candidates: list[Path] = []
    env_repo = os.environ.get("HARBOR_REPO", "").strip()
    if env_repo:
        candidates.append(Path(env_repo).expanduser())
    candidates.append(Path("/home/djn/code/harbor"))
    candidates.append(Path(DEFAULT_HARBOR_REPO).expanduser())

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        try:
            if (resolved / "pyproject.toml").exists() and (resolved / "src" / "harbor").exists():
                return resolved
        except OSError:
            continue
    raise RuntimeError(
        "Harbor repository not found; set HARBOR_REPO or install Harbor next to the orchestrator"
    )


def finalize_job_result_with_harbor(*, job_dir: Path, harbor_repo: Path | None = None) -> None:
    repo = harbor_repo or resolve_controller_harbor_repo()
    if not _FINALIZE_SCRIPT.exists():
        raise RuntimeError(f"finalize script not found: {_FINALIZE_SCRIPT}")

    command = (
        f"cd {shlex.quote(str(repo))} && "
        f"uv run python {shlex.quote(str(_FINALIZE_SCRIPT))} "
        f"--job-dir {shlex.quote(str(job_dir.resolve()))}"
    )
    completed = subprocess.run(
        ["/bin/bash", "-lc", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"Harbor job result finalize failed (exit {completed.returncode}): {detail}"
        )
    normalize_job_result_file(job_dir / "result.json")


def write_merged_job(
    *,
    merged_job_dir: Path,
    merged_job_name: str,
    source_job_dirs: list[Path],
    harbor_repo: Path | None = None,
) -> None:
    merged_job_dir.mkdir(parents=True, exist_ok=True)

    config_payload: dict[str, object] | None = None
    for source_job_dir in source_job_dirs:
        config_path = source_job_dir / "config.json"
        if config_payload is None and config_path.exists():
            config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        copy_trial_dirs(source_job_dir, merged_job_dir)

    if config_payload is None:
        raise RuntimeError("no job config found while merging Harbor jobs")

    config_payload["job_name"] = merged_job_name
    config_payload["jobs_dir"] = str(merged_job_dir.parent)
    (merged_job_dir / "config.json").write_text(
        json.dumps(config_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not _iter_trial_dirs(merged_job_dir):
        raise RuntimeError("no Harbor trial results found while merging jobs")

    finalize_job_result_with_harbor(job_dir=merged_job_dir, harbor_repo=harbor_repo)
