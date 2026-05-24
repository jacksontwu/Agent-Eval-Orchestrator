import json
import os
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, tmp_path: Path, port: int) -> ThreadedServer:
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host test\n  HostName 127.0.0.1\n  User test\n", encoding="utf-8")
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config,
        controller_shared_root=tmp_path,
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = None
    Handler.asset_syncer = asset_syncer
    Handler.ssh_config_path = ssh_config
    Handler.controller_shared_root = tmp_path
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _prepare_assets(tmp_path: Path) -> dict[str, str]:
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()
    shared = tmp_path / "runtime"
    return {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(config_dir),
        "sharedRoot": str(shared),
    }


def test_create_task_rejects_remote_without_ssh(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="remote-a",
        display_name="remote",
        host="remote",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/nonexistent/runtime"},
    )
    server = start_test_server(store, tmp_path, 9881)
    conn = HTTPConnection("127.0.0.1", 9881)
    body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["remote-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 400
    payload = json.loads(resp.read().decode("utf-8"))
    assert "ssh_host_alias" in payload["error"]
    server.shutdown()


def test_create_task_local_worker_returns_pending_sync(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": assets["sharedRoot"], "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9882)
    conn = HTTPConnection("127.0.0.1", 9882)
    body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["local-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["run"]["syncStatus"] == "pending"
    assert payload["syncJobId"]
    assert payload["batches"][0]["status"] == "pending_sync"
    server.shutdown()


def test_get_run_sync_status(store, tmp_path):
    assets = _prepare_assets(tmp_path)
    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": assets["sharedRoot"], "localToController": True},
    )
    server = start_test_server(store, tmp_path, 9883)
    conn = HTTPConnection("127.0.0.1", 9883)
    create_body = json.dumps(
        {
            "name": "sync-test",
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workerIds": ["local-a"],
            "selectedCaseIds": ["case-a"],
        }
    )
    conn.request(
        "POST",
        "/api/eval-tasks/create-and-distribute",
        body=create_body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    created = json.loads(conn.getresponse().read().decode("utf-8"))
    run_id = created["run"]["run_id"]
    sync_job_id = created["syncJobId"]

    deadline = time.time() + 5
    status = "pending"
    while time.time() < deadline and status not in {"succeeded", "failed"}:
        conn.request(
            "GET",
            f"/api/runs/{run_id}/sync",
            headers={"X-AEO-Token": "secret"},
        )
        detail = json.loads(conn.getresponse().read().decode("utf-8"))
        status = detail["status"]
        time.sleep(0.2)

    assert status == "succeeded"
    conn.request(
        "GET",
        f"/api/sync-jobs/{sync_job_id}",
        headers={"X-AEO-Token": "secret"},
    )
    job = json.loads(conn.getresponse().read().decode("utf-8"))
    assert job["jobId"] == sync_job_id
    assert job["status"] == "succeeded"
    server.shutdown()
