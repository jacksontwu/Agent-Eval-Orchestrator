import json
from pathlib import Path

import pytest

from agent_eval_orchestrator.normalizers.harbor_job_merge import (
    resolve_controller_harbor_repo,
    write_merged_job,
)


@pytest.fixture
def harbor_repo() -> Path:
    try:
        return resolve_controller_harbor_repo()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def sample_trial_payload() -> dict:
    trial_path = (
        Path(__file__).resolve().parents[2]
        / "runtime/controller/imported-jobs/batch-7a40f9895cd6/django__django-11734__Hog88fD/result.json"
    )
    if not trial_path.exists():
        pytest.skip(f"missing fixture trial result: {trial_path}")
    return json.loads(trial_path.read_text(encoding="utf-8"))


@pytest.mark.integration
def test_write_merged_job_matches_harbor_stats(tmp_path: Path, harbor_repo: Path, sample_trial_payload: dict) -> None:
    source_job = tmp_path / "source"
    source_job.mkdir()
    trial_dir = source_job / sample_trial_payload["trial_name"]
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(
        json.dumps(sample_trial_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (source_job / "config.json").write_text(
        json.dumps(
            {
                "job_name": "source",
                "metrics": [],
                "datasets": [
                    {
                        "path": str(tmp_path / "dataset-subset"),
                    }
                ],
                "agents": [{"name": "bitfun-cli"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "dataset-subset").mkdir()

    merged_job = tmp_path / "merged"
    write_merged_job(
        merged_job_dir=merged_job,
        merged_job_name="merged",
        source_job_dirs=[source_job],
        harbor_repo=harbor_repo,
    )

    result = json.loads((merged_job / "result.json").read_text(encoding="utf-8"))
    eval_stats = result["stats"]["evals"]["bitfun-cli__dataset-subset"]
    assert result["n_total_trials"] == 1
    assert eval_stats["metrics"] == [{"mean": sample_trial_payload["verifier_result"]["rewards"]["reward"]}]
    assert eval_stats["n_trials"] == 1
