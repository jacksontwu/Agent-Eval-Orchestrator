from app.model import repo_batches, repo_runs


def _seed(session, tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "c1").mkdir(parents=True)
    (dataset / "c1" / "task.toml").write_bytes(b"hello-bytes")
    run = repo_runs.create_run(session, template_id="tpl-1", owner="alice", display_name="R1")
    session.commit()
    batch = repo_batches.create_batch(
        session, run_id=run.run_id, owner="alice", executor_kind="harbor",
        selected_case_ids=["c1"], batch_options={}, batch_root="/tmp/b",
        executor_metadata={"datasetPath": str(dataset)},
    )
    session.commit()
    return batch


def test_get_manifest(client, session, tmp_path):
    batch = _seed(session, tmp_path)
    resp = client.get(f"/api/workers/assets/am-{batch.batch_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assetManifestId"] == f"am-{batch.batch_id}"
    assert any(e["path"] == "cases/c1/task.toml" for e in data["entries"])


def test_get_file_streams_bytes(client, session, tmp_path):
    batch = _seed(session, tmp_path)
    resp = client.get(f"/api/workers/assets/am-{batch.batch_id}/file", params={"path": "cases/c1/task.toml"})
    assert resp.status_code == 200
    assert resp.content == b"hello-bytes"
