import json
from datetime import datetime
from pathlib import Path

import pytest

from agent_eval_orchestrator.normalizers.harbor_timestamps import (
    normalize_job_result_file,
    normalize_job_result_payload,
    normalize_jobs_dir,
    to_harbor_naive_utc_iso,
)
from agent_eval_orchestrator.normalizers.harbor_job_merge import write_merged_job


@pytest.mark.unit
def test_normalize_job_result_strips_utc_suffix() -> None:
    payload = {
        "id": "job-1",
        "started_at": "2026-05-25T08:56:31.915412Z",
        "updated_at": "2026-05-25T08:56:31.915412+00:00",
        "finished_at": None,
    }
    normalized = normalize_job_result_payload(payload)
    assert normalized["started_at"] == "2026-05-25T08:56:31.915412"
    assert normalized["updated_at"] == "2026-05-25T08:56:31.915412"


@pytest.mark.unit
def test_normalize_jobs_dir_updates_mixed_timezone_jobs(tmp_path: Path) -> None:
    aware_job = tmp_path / "aware-job"
    legacy_job = tmp_path / "legacy-job"
    for name, started_at in (
        ("aware-job", "2026-05-25T08:56:31.915412Z"),
        ("legacy-job", "2026-05-21T23:12:10.429716"),
    ):
        job_dir = tmp_path / name
        job_dir.mkdir()
        (job_dir / "result.json").write_text(
            json.dumps({"started_at": started_at, "updated_at": started_at}),
            encoding="utf-8",
        )

    changed = normalize_jobs_dir(tmp_path)

    assert changed == 1
    assert json.loads((aware_job / "result.json").read_text())["started_at"] == (
        "2026-05-25T08:56:31.915412"
    )
    assert json.loads((legacy_job / "result.json").read_text())["started_at"] == (
        "2026-05-21T23:12:10.429716"
    )


@pytest.mark.unit
def test_write_merged_job_uses_naive_utc_timestamps(tmp_path: Path, monkeypatch) -> None:
    from agent_eval_orchestrator.normalizers import harbor_job_merge

    def _fake_finalize(*, job_dir: Path, harbor_repo=None) -> None:
        (job_dir / "result.json").write_text(
            json.dumps(
                {
                    "id": "job-1",
                    "started_at": "2026-05-25T08:56:31.915412Z",
                    "updated_at": "2026-05-25T09:00:00.915412Z",
                    "finished_at": "2026-05-25T09:00:00.915412Z",
                    "n_total_trials": 1,
                    "stats": {"n_completed_trials": 1},
                }
            ),
            encoding="utf-8",
        )
        normalize_job_result_file(job_dir / "result.json")

    monkeypatch.setattr(harbor_job_merge, "finalize_job_result_with_harbor", _fake_finalize)

    source_job = tmp_path / "source"
    source_job.mkdir()
    trial_dir = source_job / "trial-a"
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "trial_name": "trial-a",
                "started_at": "2026-05-25T08:56:31.915412Z",
                "finished_at": "2026-05-25T09:00:00.915412Z",
                "agent_info": {"name": "agent"},
                "verifier_result": {"rewards": {"primary": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    (source_job / "config.json").write_text(
        json.dumps({"job_name": "source", "agents": []}),
        encoding="utf-8",
    )

    merged_job = tmp_path / "merged"
    write_merged_job(
        merged_job_dir=merged_job,
        merged_job_name="merged",
        source_job_dirs=[source_job],
    )
    result = json.loads((merged_job / "result.json").read_text(encoding="utf-8"))
    assert result["started_at"] == to_harbor_naive_utc_iso(
        datetime.fromisoformat("2026-05-25T08:56:31.915412+00:00")
    )
    assert "Z" not in result["started_at"]
    assert "+" not in result["started_at"]
