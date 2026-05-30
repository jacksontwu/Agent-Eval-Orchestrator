import json
import os
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.rerun_artifacts import derived_jobs_dir_for_run
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


def _prepare_rerun_assets(tmp_path, case_ids):
    dataset = tmp_path / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    bitfun_config = tmp_path / "bitfun-config"
    bitfun_config.mkdir()
    jobs_dir = tmp_path / "harbor" / "jobs"
    return {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(bitfun_config),
        "jobsDir": str(jobs_dir),
    }


def _make_worker_local(store, tmp_path):
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={
            "sharedRoot": str(tmp_path / "shared"),
            "localToController": True,
        },
    )


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


def test_post_rerun_exceptions_accepts_config_body(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    previous_target = str(tmp_path / "shared" / "sync" / run["run_id"])
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "datasetPath": "/tmp/old-dataset",
            "bitfunCliPath": "/tmp/old-bitfun-cli",
            "bitfunConfigDir": "/tmp/old-bitfun-config",
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a", "ok"],
                    "targetRoot": previous_target,
                    "transport": "local",
                }
            },
        },
    )
    server = start_test_server(store, tmp_path, 9895)
    conn = HTTPConnection("127.0.0.1", 9895)
    body = json.dumps(
        {
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        }
    )
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body=body,
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    assert payload["runId"] != run["run_id"]
    assert payload["parentRunId"] == run["run_id"]
    original_template = store.get_task_template(run["template_id"])
    assert original_template["dataset_ref"] == "/tmp/dataset"
    derived_run = store.get_run(payload["runId"])
    assert derived_run["parent_run_id"] == run["run_id"]
    template = store.get_task_template(derived_run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    executor_config = template["executor_config"]
    assert executor_config["nConcurrent"] == 2
    assert executor_config["combinedJobsDir"] == str(
        derived_jobs_dir_for_run(store=store, run=derived_run)
    )
    assert executor_config["combinedJobsDir"] != assets["jobsDir"]
    updated_run = store.get_run(run["run_id"])
    assert updated_run["sync_manifest"]["datasetPath"] == "/tmp/old-dataset"
    manifest = derived_run["sync_manifest"]
    assert manifest["datasetPath"] == assets["datasetPath"]
    assert manifest["bitfunCliPath"] == assets["bitfunCliPath"]
    assert manifest["bitfunConfigDir"] == assets["bitfunConfigDir"]
    assert manifest["workers"]["worker-a"]["targetRoot"] != previous_target
    assert manifest["workers"]["worker-a"]["transport"] == "local"
    job = store.get_run_rerun_job(payload["rerunJobId"])
    assert job["run_id"] == payload["runId"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["run_id"] == payload["runId"]
    assert rerun_batch["batch_options"]["concurrency"] == 2
    server.shutdown()


def test_post_rerun_exceptions_rejects_invalid_config_without_job(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    server = start_test_server(store, tmp_path, 9896)
    conn = HTTPConnection("127.0.0.1", 9896)
    body = json.dumps(
        {
            "datasetPath": str(tmp_path / "missing-dataset"),
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        }
    )
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body=body,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 400
    assert "datasetPath" in payload["error"]
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert [
        item for item in store.list_runs()
        if item.get("parent_run_id") == run["run_id"]
    ] == []
    server.shutdown()


def test_post_rerun_exceptions_rejects_non_object_body_without_job(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9897)
    conn = HTTPConnection("127.0.0.1", 9897)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="[]",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 400
    assert payload["error"] == "request body must be a JSON object"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    server.shutdown()


def test_post_rerun_exceptions_rejects_invalid_json_without_job(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9898)
    conn = HTTPConnection("127.0.0.1", 9898)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body="{",
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 400
    assert payload["error"] == "request body must be valid JSON"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
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


def test_get_rerun_status_includes_list_valued_rerun_batches(store, tmp_path):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    rerun_a = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    rerun_b = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-b"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    store.create_run_rerun_job(
        job_id="rerun-list",
        run_id=run["run_id"],
        case_ids=["exc-a", "exc-b"],
        worker_shards={"worker-a": ["exc-a", "exc-b"]},
        rerun_batches={"worker-a": [rerun_a["batch_id"], rerun_b["batch_id"]]},
    )
    store.update_run_rerun_fields(
        run_id=run["run_id"],
        rerun_status="running",
        rerun_job_id="rerun-list",
    )
    server = start_test_server(store, tmp_path, 9899)
    conn = HTTPConnection("127.0.0.1", 9899)
    conn.request(
        "GET",
        f"/api/runs/{run['run_id']}/rerun",
        headers={"X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert [item["batchId"] for item in payload["rerunBatches"]] == [
        rerun_a["batch_id"],
        rerun_b["batch_id"],
    ]
    assert {item["workerId"] for item in payload["rerunBatches"]} == {"worker-a"}
    assert "rerun_batches" not in payload["job"]
    assert set(payload["job"]) == {
        "jobId",
        "runId",
        "status",
        "syncJobId",
        "caseIds",
        "workerShards",
        "selectedErrorTypes",
        "errorText",
        "createdAt",
        "finishedAt",
    }
    assert payload["job"]["jobId"] == "rerun-list"
    assert payload["job"]["runId"] == run["run_id"]
    assert payload["job"]["caseIds"] == ["exc-a", "exc-b"]
    assert payload["job"]["workerShards"] == {"worker-a": ["exc-a", "exc-b"]}
    assert payload["job"]["selectedErrorTypes"] == []
    server.shutdown()


def test_get_rerun_status_includes_parent_run_id_for_derived_run(store, tmp_path):
    parent, parent_batch = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    child_template = store.clone_task_template(parent["template_id"], name="child")
    child_run = store.create_run(
        template_id=child_template["template_id"],
        display_name="child rerun",
        parent_run_id=parent["run_id"],
    )
    rerun_batch = store.create_batch(
        run_id=child_run["run_id"],
        selected_case_ids=["exc-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=parent_batch["batch_id"],
    )
    store.create_run_rerun_job(
        job_id="rerun-derived",
        run_id=child_run["run_id"],
        case_ids=["exc-a"],
        worker_shards={"worker-a": ["exc-a"]},
        rerun_batches={"worker-a": rerun_batch["batch_id"]},
    )
    store.update_run_rerun_fields(
        run_id=child_run["run_id"],
        rerun_status="running",
        rerun_job_id="rerun-derived",
    )
    server = start_test_server(store, tmp_path, 9900)
    conn = HTTPConnection("127.0.0.1", 9900)
    conn.request(
        "GET",
        f"/api/runs/{child_run['run_id']}/rerun",
        headers={"X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert payload["runId"] == child_run["run_id"]
    assert payload["parentRunId"] == parent["run_id"]
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


def test_post_rerun_exceptions_filters_by_selected_error_types(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    server = start_test_server(store, tmp_path, 9896)
    conn = HTTPConnection("127.0.0.1", 9896)
    with patch.object(AssetSyncer, "start_rerun_sync_async"):
        conn.request(
            "POST",
            f"/api/runs/{run['run_id']}/rerun-exceptions",
            body=json.dumps({"selectedErrorTypes": ["TimeoutError"]}),
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
    assert resp.status == 201
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["exceptionCount"] == 1
    assert payload["selectedErrorTypes"] == ["TimeoutError"]
    server.shutdown()


def test_post_rerun_exceptions_rejects_empty_selected_error_types(store, tmp_path):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    server = start_test_server(store, tmp_path, 9897)
    conn = HTTPConnection("127.0.0.1", 9897)
    conn.request(
        "POST",
        f"/api/runs/{run['run_id']}/rerun-exceptions",
        body=json.dumps({"selectedErrorTypes": []}),
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    assert resp.status == 400
    server.shutdown()
