from app.model import repo_workers


def test_register_then_heartbeat(client, session):
    r = client.post("/api/workers/register", json={
        "workerId": "w1", "displayName": "W1", "host": "h", "slotsTotal": 2, "capabilities": {}})
    assert r.status_code == 200 and r.json()["workerId"] == "w1"
    hb = client.post("/api/workers/heartbeat", json={"workerId": "w1", "slotsUsed": 1, "status": "online"})
    assert hb.status_code == 200 and hb.json()["ok"] is True
    w = repo_workers.get_worker(session, "w1")
    assert w.slots_used == 1 and w.status == "online"
