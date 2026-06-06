from app.model import repo_asset_sync_jobs as repo


def test_create_pending(session):
    job = repo.create_job(session, run_id="run-1", steps=[{"name": "copy"}])
    session.commit()
    assert job.job_id.startswith("sync-")
    assert job.status == "pending"
    assert repo.get_job(session, job.job_id).run_id == "run-1"


def test_update_status_and_log(session):
    job = repo.create_job(session, run_id="run-1", steps=[])
    session.commit()
    repo.update_job(session, job.job_id, status="running", log_append="line1\n")
    repo.update_job(session, job.job_id, log_append="line2\n")
    session.commit()
    got = repo.get_job(session, job.job_id)
    assert got.status == "running"
    assert got.log_text == "line1\nline2\n"
