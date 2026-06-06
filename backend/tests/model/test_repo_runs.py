from app.model import repo_runs as repo


def test_create_and_get(session):
    run = repo.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    assert run.run_id.startswith("run-")
    assert run.sync_status == "" and run.rerun_status == "idle" and run.sync_manifest == {}
    assert repo.get_run(session, run.run_id).display_name == "R1"


def test_set_latest_batch(session):
    run = repo.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    repo.set_latest_batch(session, run.run_id, "batch-9")
    session.commit()
    assert repo.get_run(session, run.run_id).latest_batch_id == "batch-9"


def test_set_sync(session):
    run = repo.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    repo.set_sync(session, run.run_id, status="syncing", job_id="sync-1", manifest={"a": 1})
    session.commit()
    got = repo.get_run(session, run.run_id)
    assert got.sync_status == "syncing" and got.sync_job_id == "sync-1" and got.sync_manifest == {"a": 1}


def test_list_filters_by_owner(session):
    repo.create_run(session, template_id="t", owner="alice", display_name="A")
    repo.create_run(session, template_id="t", owner="bob", display_name="B")
    session.commit()
    assert {r.display_name for r in repo.list_runs(session, owner="alice")} == {"A"}
