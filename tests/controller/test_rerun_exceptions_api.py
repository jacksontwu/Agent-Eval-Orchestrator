import json
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from conftest import seed_finished_run_with_cases


def start_test_server(store, tmp_path, port):
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host test\n  HostName 127.0.0.1\n  User test\n", encoding="utf-8")
    asset_syncer = AssetSyncer(
        store=store,
        ssh_config_path=ssh_config,
        controller_shared_root=tmp_path,
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=asset_syncer)
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = None
    Handler.worker_updater = None
    Handler.asset_syncer = asset_syncer
    Handler.run_rerun_coordinator = coordinator
    Handler.ssh_config_path = ssh_config
    Handler.controller_shared_root = tmp_path
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_post_rerun_exceptions_happy_path(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9891)
    conn = HTTPConnection("127.0.0.1", 9891)
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body="{}",
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    assert payload["rerunStatus"] == "syncing"
    server.shutdown()


def test_post_rerun_exceptions_rejects_active_rerun(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="running")
    server = start_test_server(store, tmp_path, 9892)
    conn = HTTPConnection("127.0.0.1", 9892)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="{}",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()


def test_heartbeat_merges_exception_rerun_into_parent(store, tmp_path):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
        ],
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    store.create_run_rerun_job(
        job_id="rerun-1",
        run_id=run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun["batch_id"]},
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="running", rerun_job_id="rerun-1")
    server = start_test_server(store, tmp_path, 9893)
    conn = HTTPConnection("127.0.0.1", 9893)
    body = json.dumps(
        {
            "batchId": rerun["batch_id"],
            "workerId": "worker-a",
            "status": "succeeded",
            "finished": True,
            "cases": [
                {
                    "caseId": "exc-a",
                    "status": "succeeded",
                    "score": 1.0,
                    "metrics": {},
                    "artifactIndex": {},
                }
            ],
            "summary": {"succeeded": 1, "failed": 0, "errored": 0, "total": 1},
        }
    )
    conn.request(
        "POST",
        "/api/workers/heartbeat",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    parent_cases = store.list_case_runs(parent["batch_id"])
    by_id = {case["case_id"]: case for case in parent_cases}
    assert by_id["exc-a"]["status"] == "succeeded"
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "succeeded"
    server.shutdown()


def test_post_rerun_before_run_finished(store, tmp_path):
    template = store.create_task_template(
        owner="default",
        name="x",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
    )
    server = start_test_server(store, tmp_path, 9894)
    conn = HTTPConnection("127.0.0.1", 9894)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="{}",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 409
    server.shutdown()
