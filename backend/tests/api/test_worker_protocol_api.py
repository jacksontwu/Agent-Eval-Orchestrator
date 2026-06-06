from app.model import repo_batches, repo_runs, repo_workers


def test_register_then_heartbeat(client, session):
    r = client.post("/api/workers/register", json={
        "workerId": "w1", "displayName": "W1", "host": "h", "slotsTotal": 2, "capabilities": {}})
    assert r.status_code == 200 and r.json()["workerId"] == "w1"
    hb = client.post("/api/workers/heartbeat", json={"workerId": "w1", "slotsUsed": 1, "status": "online"})
    assert hb.status_code == 200 and hb.json()["ok"] is True
    w = repo_workers.get_worker(session, "w1")
    assert w.slots_used == 1 and w.status == "online"


def test_claim_returns_asset_contract(client, session, tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "c1").mkdir(parents=True)
    (dataset / "c1" / "task.toml").write_bytes(b"hi")
    repo_workers.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(
        session, run_id=run.run_id, owner="alice", executor_kind="harbor",
        selected_case_ids=["c1"], batch_options={}, batch_root="/tmp/b",
        executor_metadata={"datasetPath": str(dataset)},
    )
    repo_batches.assign(session, batch.batch_id, "w1")
    session.commit()

    resp = client.post("/api/workers/claim", json={"workerId": "w1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batchId"] == batch.batch_id
    assert data["assetManifestId"] == f"am-{batch.batch_id}"
    assert data["assetUrl"].endswith(f"am-{batch.batch_id}")
    assert data["assetManifest"]["entries"]
    assert repo_batches.get_batch(session, batch.batch_id).status == "running"


def test_claim_no_work_returns_null(client, session):
    repo_workers.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=1, capabilities={})
    session.commit()
    resp = client.post("/api/workers/claim", json={"workerId": "w1"})
    assert resp.status_code == 200
    assert resp.json()["batchId"] is None
