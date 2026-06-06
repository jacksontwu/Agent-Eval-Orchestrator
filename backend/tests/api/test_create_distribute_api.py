from app.model import repo_batches, repo_runs, repo_workers


def _seed_workers(session, tmp_path):
    for wid in ("w1", "w2"):
        repo_workers.upsert_worker(session, worker_id=wid, display_name=wid, host="h",
                                   slots_total=1, capabilities={"sharedRoot": str(tmp_path / f"{wid}-runtime")})
    session.commit()


def test_create_and_distribute(client, session, tmp_path):
    _seed_workers(session, tmp_path)
    dataset = tmp_path / "dataset"
    (dataset / "c1").mkdir(parents=True)
    (dataset / "c1" / "task.toml").write_text("", encoding="utf-8")
    (dataset / "c2").mkdir()
    (dataset / "c2" / "task.toml").write_text("", encoding="utf-8")
    cli = tmp_path / "bitfun-cli"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    config_dir = tmp_path / "bitfun-config"
    (config_dir / "config").mkdir(parents=True)

    payload = {
        "name": "task-1",
        "datasetPath": str(dataset),
        "bitfunCliPath": str(cli),
        "bitfunConfigDir": str(config_dir),
        "selectedCaseIds": ["c1", "c2"],
        "perWorkerConcurrency": 1,
    }
    resp = client.post("/api/eval-tasks/create-and-distribute", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    run_id = data["runId"]
    batch_ids = data["batchIds"]
    assert run_id.startswith("run-")
    assert len(batch_ids) >= 1

    batches = repo_batches.list_batches_for_run(session, run_id)
    assert all(b.status == "queued" for b in batches)
    union: set[str] = set()
    for b in batches:
        union.update(b.selected_case_ids)
        assert "datasetPath" in b.executor_metadata
        assert "bitfunCliPath" in b.executor_metadata
        assert "bitfunConfigDir" in b.executor_metadata
    assert union == {"c1", "c2"}
