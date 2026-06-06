from app.model import repo_batches, repo_runs, repo_workers
from app.service.orchestration import scheduler


def _queue_batch(session, run_id):
    return repo_batches.create_batch(session, run_id=run_id, owner="a", executor_kind="harbor",
                                     selected_case_ids=["c"], batch_options={}, batch_root="/tmp/b")


def test_assign_once_respects_free_slots(session):
    repo_workers.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    repo_workers.upsert_worker(session, worker_id="w2", display_name="W2", host="h", slots_total=1, capabilities={})
    run = repo_runs.create_run(session, template_id="t", owner="a", display_name="R")
    session.commit()
    for _ in range(3):
        _queue_batch(session, run.run_id)
    session.commit()

    assigned = scheduler.assign_once(session)
    session.commit()
    assert assigned == 2
    queued = repo_batches.list_by_status(session, "queued")
    assigned_batches = repo_batches.list_by_status(session, "assigned")
    assert len(queued) == 1 and len(assigned_batches) == 2


def test_assign_prefers_higher_weight(session):
    repo_workers.upsert_worker(session, worker_id="low", display_name="L", host="h", slots_total=1, capabilities={})
    repo_workers.upsert_worker(session, worker_id="high", display_name="H", host="h", slots_total=1, capabilities={})
    repo_workers.get_worker(session, "high").allocation_weight = 5.0
    run = repo_runs.create_run(session, template_id="t", owner="a", display_name="R")
    session.commit()
    batch = _queue_batch(session, run.run_id)
    session.commit()

    scheduler.assign_once(session)
    session.commit()
    assert repo_batches.get_batch(session, batch.batch_id).assigned_worker_id == "high"
