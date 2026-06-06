from app.model import repo_batches, repo_runs, repo_workers
from app.service.orchestration import reaper


def test_reap_once_offlines_stale_worker_and_requeues(session):
    repo_workers.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    repo_workers.update_runtime(session, "w1", status="online",
                                last_heartbeat_at="2000-01-01T00:00:00+00:00")
    run = repo_runs.create_run(session, template_id="t", owner="a", display_name="R")
    session.commit()
    batch = repo_batches.create_batch(session, run_id=run.run_id, owner="a", executor_kind="harbor",
                                      selected_case_ids=["c"], batch_options={}, batch_root="/tmp/b")
    repo_batches.assign(session, batch.batch_id, "w1")
    repo_batches.set_status(session, batch.batch_id, "running")
    session.commit()

    reaped = reaper.reap_once(session, timeout_sec=45)
    session.commit()
    assert reaped >= 1
    assert repo_workers.get_worker(session, "w1").status == "offline"
    got = repo_batches.get_batch(session, batch.batch_id)
    assert got.status == "queued" and got.assigned_worker_id is None
