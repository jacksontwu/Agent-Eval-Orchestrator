from app.model import repo_workers as repo


def test_upsert_and_get(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h",
                       slots_total=2, capabilities={"cpu": 8})
    session.commit()
    w = repo.get_worker(session, "w1")
    assert w is not None and w.slots_total == 2 and w.capabilities["cpu"] == 8


def test_list_enabled(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    repo.upsert_worker(session, worker_id="w2", display_name="W2", host="h", slots_total=1, capabilities={})
    repo.set_enabled(session, "w2", False)
    session.commit()
    ids = [w.worker_id for w in repo.list_workers(session, only_enabled=True)]
    assert ids == ["w1"]


def test_touch_heartbeat_updates_slots(session):
    repo.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=4, capabilities={})
    session.commit()
    repo.update_runtime(session, "w1", slots_used=3, status="online", last_heartbeat_at="2026-01-01T00:00:00+00:00")
    session.commit()
    w = repo.get_worker(session, "w1")
    assert w.slots_used == 3 and w.status == "online"
