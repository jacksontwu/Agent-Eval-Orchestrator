from app.model import repo_rerun_jobs as repo


def test_create_pending(session):
    job = repo.create_job(session, run_id="run-1", case_ids=["c1", "c2"],
                          worker_shards={"w1": ["c1"], "w2": ["c2"]})
    session.commit()
    assert job.job_id.startswith("rerun-")
    assert job.status == "pending" and job.rerun_batches == []
    assert repo.get_job(session, job.job_id).case_ids == ["c1", "c2"]


def test_update(session):
    job = repo.create_job(session, run_id="run-1", case_ids=["c1"], worker_shards={})
    session.commit()
    repo.update_job(session, job.job_id, status="running", sync_job_id="sync-9",
                    rerun_batches=[{"batchId": "b1"}])
    session.commit()
    got = repo.get_job(session, job.job_id)
    assert got.status == "running" and got.sync_job_id == "sync-9"
    assert got.rerun_batches == [{"batchId": "b1"}]
