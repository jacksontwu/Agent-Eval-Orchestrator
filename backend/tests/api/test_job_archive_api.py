import hashlib
import io
import json
import tarfile
from pathlib import Path

from app.model import repo_batches, repo_runs


def _build_job_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # job-level result.json
        result = json.dumps({"stats": {}}).encode()
        info = tarfile.TarInfo("result.json")
        info.size = len(result)
        tar.addfile(info, io.BytesIO(result))
        # one trial dir result.json
        trial = json.dumps({
            "trial_name": "case-a__x",
            "verifier_result": {"rewards": {"reward": 1.0}},
        }).encode()
        tinfo = tarfile.TarInfo("case-a__x/result.json")
        tinfo.size = len(trial)
        tar.addfile(tinfo, io.BytesIO(trial))
    return buf.getvalue()


def test_job_archive_ingest(client, session, tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    from app.core.config import get_settings
    get_settings.cache_clear()

    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["case-a"], batch_options={}, batch_root="/tmp/b")
    session.commit()

    archive = _build_job_tar()
    sha = hashlib.sha256(archive).hexdigest()
    resp = client.post(
        "/api/workers/job-archive",
        data={"batchId": batch.batch_id, "sha256": sha},
        files={"archive": ("job.tar.gz", archive, "application/gzip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["batchId"] == batch.batch_id

    imported = tmp_path / "controller" / "imported-jobs" / batch.batch_id
    assert imported.is_dir()
    assert (imported / "result.json").exists()


def test_job_archive_bad_sha(client, session, tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    from app.core.config import get_settings
    get_settings.cache_clear()
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["case-a"], batch_options={}, batch_root="/tmp/b")
    session.commit()
    archive = _build_job_tar()
    resp = client.post(
        "/api/workers/job-archive",
        data={"batchId": batch.batch_id, "sha256": "deadbeef"},
        files={"archive": ("job.tar.gz", archive, "application/gzip")},
    )
    assert resp.status_code == 400
