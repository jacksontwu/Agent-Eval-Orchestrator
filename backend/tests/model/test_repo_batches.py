from app.model import repo_batches as repo


def _make(session, **kw):
    return repo.create_batch(
        session, run_id=kw.get("run_id", "run-1"), owner="alice", executor_kind="harbor",
        selected_case_ids=kw.get("cases", ["c1"]), batch_options={}, batch_root="/tmp/b",
    )


def test_create_defaults_queued(session):
    b = _make(session)
    session.commit()
    assert b.batch_id.startswith("batch-")
    assert b.status == "queued" and b.summary == {} and b.artifact_index == {}
    assert repo.get_batch(session, b.batch_id).run_id == "run-1"


def test_list_by_status_and_for_run(session):
    b = _make(session)
    session.commit()
    assert [x.batch_id for x in repo.list_by_status(session, "queued")] == [b.batch_id]
    assert [x.batch_id for x in repo.list_batches_for_run(session, "run-1")] == [b.batch_id]


def test_assign_sets_worker_and_status(session):
    b = _make(session)
    session.commit()
    repo.assign(session, b.batch_id, "w1")
    session.commit()
    got = repo.get_batch(session, b.batch_id)
    assert got.assigned_worker_id == "w1" and got.status == "assigned"


def test_set_summary(session):
    b = _make(session)
    session.commit()
    repo.set_summary(session, b.batch_id, {"succeeded": 1}, {"f": "x"})
    session.commit()
    got = repo.get_batch(session, b.batch_id)
    assert got.summary == {"succeeded": 1} and got.artifact_index == {"f": "x"}
