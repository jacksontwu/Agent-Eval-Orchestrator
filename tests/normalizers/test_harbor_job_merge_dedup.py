import json
from pathlib import Path

from agent_eval_orchestrator.normalizers.harbor_job_merge import copy_trial_dirs


def _write_trial(job_dir: Path, *, trial_name: str, task_name: str) -> None:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"trial_name": trial_name, "task_name": task_name}),
        encoding="utf-8",
    )


def test_copy_trial_dirs_keeps_distinct_cases_with_shared_trial_prefix(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shared_prefix = "instance_internetarchive__openli"
    _write_trial(
        source,
        trial_name=f"{shared_prefix}__trialA",
        task_name=f"{shared_prefix}-hash-a-vsuffix-a",
    )
    _write_trial(
        source,
        trial_name=f"{shared_prefix}__trialB",
        task_name=f"{shared_prefix}-hash-b-vsuffix-b",
    )

    target = tmp_path / "target"
    copy_trial_dirs(source, target)

    assert len(list(target.iterdir())) == 2
