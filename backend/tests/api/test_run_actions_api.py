from app.model import repo_batches, repo_case_runs, repo_runs


def _seed_run_with_errored_case(session):
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="alice", executor_kind="harbor",
                                      selected_case_ids=["c1", "c2"], batch_options={}, batch_root="/tmp/b")
    repo_batches.assign(session, batch.batch_id, "w1")
    session.commit()
    repo_case_runs.replace_for_batch(session, batch.batch_id, [
        {"case_id": "c1", "status": "succeeded", "score": 1.0, "metrics": {}, "artifact_index": {}, "error_text": None},
        {"case_id": "c2", "status": "errored", "score": None, "metrics": {}, "artifact_index": {}, "error_text": "boom"},
    ])
    session.commit()
    return run, batch


def test_rerun_exceptions_creates_job(client, session):
    run, batch = _seed_run_with_errored_case(session)
    resp = client.post(f"/api/runs/{run.run_id}/rerun-exceptions", json={})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["jobId"].startswith("rerun-")
    assert data["caseIds"] == ["c2"]
    assert repo_runs.get_run(session, run.run_id).rerun_status in ("pending", "running")


def test_get_sync_status(client, session):
    run, batch = _seed_run_with_errored_case(session)
    repo_runs.set_sync(session, run.run_id, status="succeeded")
    session.commit()
    resp = client.get(f"/api/runs/{run.run_id}/sync")
    assert resp.status_code == 200, resp.text
    assert resp.json()["syncStatus"] == "succeeded"


def test_rerun_exceptions_run_404(client):
    assert client.post("/api/runs/run-nope/rerun-exceptions", json={}).status_code == 404
