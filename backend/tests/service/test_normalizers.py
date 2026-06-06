import json
from pathlib import Path

from app.service.normalizers.harbor import normalize_harbor_job


def _write_trial(job_dir: Path, trial_name: str, *, reward, exception_type) -> None:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    payload: dict = {
        "trial_name": trial_name,
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else {},
    }
    if exception_type:
        payload["exception_info"] = {
            "exception_type": exception_type,
            "exception_message": "boom",
        }
    (trial_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")


def test_normalize_harbor_job_distinguishes_failed_and_errored(tmp_path: Path) -> None:
    job_dir = tmp_path / "batch-a"
    job_dir.mkdir()
    (job_dir / "result.json").write_text(json.dumps({"stats": {}}), encoding="utf-8")
    _write_trial(job_dir, "django__django-11880__abc", reward=0.0, exception_type=None)
    _write_trial(job_dir, "django__django-17087__def", reward=None, exception_type="RewardFileNotFoundError")

    summary, cases, _artifact_index = normalize_harbor_job(job_dir, "batch-a")
    by_name = {case["caseId"]: case for case in cases}

    assert by_name["django__django-11880"]["status"] == "failed"
    assert by_name["django__django-11880"]["score"] == 0.0
    assert by_name["django__django-17087"]["status"] == "errored"
    assert by_name["django__django-17087"]["errorType"] == "RewardFileNotFoundError"
    assert summary["succeeded"] == 0
    assert summary["failed"] == 1
    assert summary["errored"] == 1
