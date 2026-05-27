import json
from pathlib import Path

import pytest

from agent_eval_orchestrator.normalizers.harbor_job_merge import copy_trial_dirs


def _write_trial(job_dir: Path, trial_name: str) -> None:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"trial_name": trial_name}),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_copy_trial_dirs_replaces_prior_trial_with_same_case_id(tmp_path: Path) -> None:
    case_id = "django__django-14373"
    parent_job_dir = tmp_path / "parent"
    rerun_job_dir = tmp_path / "rerun"
    parent_job_dir.mkdir()
    rerun_job_dir.mkdir()

    old_trial = f"{case_id}__OLD_SUFFIX"
    new_trial = f"{case_id}__NEW_SUFFIX"
    _write_trial(parent_job_dir, old_trial)
    _write_trial(parent_job_dir, "other__case__keep")
    _write_trial(rerun_job_dir, new_trial)

    copy_trial_dirs(rerun_job_dir, parent_job_dir)

    remaining = {child.name for child in parent_job_dir.iterdir() if child.is_dir()}
    assert remaining == {"other__case__keep", new_trial}
    assert not (parent_job_dir / old_trial).exists()
    assert (parent_job_dir / new_trial / "result.json").exists()
