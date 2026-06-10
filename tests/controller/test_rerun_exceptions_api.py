import json
import os
import base64
import io
import tarfile
from pathlib import Path
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.asset_syncer import AssetSyncer
from agent_eval_orchestrator.controller.rerun_artifacts import derived_jobs_dir_for_run
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer, _job_sources_for_run
from agent_eval_orchestrator.core.ids import sanitize_name
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
    (bitfun_config / "config").mkdir(parents=True)
    (bitfun_config / "config" / "app.json").write_text("{}", encoding="utf-8")
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


def _write_jobs_trial(job_dir: Path, trial_name: str, *, task_name: str) -> Path:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"trial_name": trial_name, "task_name": task_name}),
        encoding="utf-8",
    )
    return trial_dir


def _tar_dir_base64(path: Path) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(path, arcname=path.name)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_job_sources_for_derived_run_uses_derived_sources_not_original_job_dir(store, tmp_path):
    run, parent_batch = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    original_job_dir = tmp_path / "original-jobs" / parent_batch["batch_id"]
    original_job_dir.mkdir(parents=True)
    store.update_batch_progress(
        batch_id=parent_batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        artifact_index={"jobDir": str(original_job_dir)},
    )
    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(run["run_id"])
    derived_run = store.get_run(result["runId"])
    derived_primary = store.list_primary_batches_for_run(result["runId"])[0]
    derived_imported_dir = (
        store.layout.controller_dir / "imported-jobs" / derived_primary["batch_id"]
    )
    derived_imported_dir.mkdir(parents=True)
    derived_jobs_dir = derived_jobs_dir_for_run(store=store, run=derived_run)
    copied_baseline_dir = derived_jobs_dir / "original-merged"
    copied_baseline_dir.mkdir(parents=True)
    (copied_baseline_dir / "config.json").write_text(
        json.dumps({"job_name": "original-merged"}),
        encoding="utf-8",
    )

    grouped_sources = _job_sources_for_run(
        store=store,
        run_id=result["runId"],
        jobs_dir=derived_jobs_dir,
    )

    assert grouped_sources == [
        (
            sanitize_name(derived_run["display_name"]),
            [copied_baseline_dir.resolve(), derived_imported_dir.resolve()],
        )
    ]


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


def test_get_case_run_returns_lazy_case_detail(store, tmp_path):
    _run, batch = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": "case-a",
                "status": "succeeded",
                "score": 1.0,
                "artifact_index": {"trialDir": "/tmp/jobs/batch/case-a__old"},
                "metrics": {"inputTokens": 12},
            }
        ],
    )
    server = start_test_server(store, tmp_path, 9901)
    conn = HTTPConnection("127.0.0.1", 9901)
    conn.request(
        "GET",
        f"/api/case-runs?batchId={batch['batch_id']}&caseId=case-a",
        headers={"X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()

    assert resp.status == 200
    payload = json.loads(resp.read().decode("utf-8"))
    assert payload["case_id"] == "case-a"
    assert payload["batchId"] == batch["batch_id"]
    assert payload["artifact_index"]["trialDir"] == "/tmp/jobs/batch/case-a__old"
    assert payload["metrics"]["inputTokens"] == 12
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


def test_heartbeat_merges_derived_exception_rerun_into_final_rerun_job(store, tmp_path):
    run, original_parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {
                "case_id": "exc-a",
                "status": "errored",
                "error_text": "ValueError: boom",
            },
        ],
    )
    original_jobs_dir = tmp_path / "original-harbor" / "jobs"
    original_job_dir = original_jobs_dir / sanitize_name(str(run["display_name"]))
    (original_job_dir / "config.json").parent.mkdir(parents=True)
    (original_job_dir / "config.json").write_text(
        json.dumps({"job_name": original_job_dir.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    original_ok_trial = _write_jobs_trial(original_job_dir, "ok__old", task_name="ok")
    original_exc_trial = _write_jobs_trial(original_job_dir, "exc-a__old", task_name="exc-a")
    (original_exc_trial / "exception.txt").write_text(
        "Traceback (most recent call last):\nValueError: boom\n",
        encoding="utf-8",
    )
    unrelated_job_dir = original_jobs_dir / "unrelated-job"
    _write_jobs_trial(unrelated_job_dir, "other__old", task_name="other")
    (unrelated_job_dir / "config.json").write_text(
        json.dumps({"job_name": unrelated_job_dir.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    store.update_task_template_executor_config(
        str(run["template_id"]),
        {"combinedJobsDir": str(original_jobs_dir)},
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)
    result = coordinator.start_rerun(run["run_id"])
    derived_run = store.get_run(result["runId"])
    derived_template = store.get_task_template(str(derived_run["template_id"]))
    derived_jobs_dir = Path(derived_template["executor_config"]["combinedJobsDir"])
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    cloned_parent = store.get_batch(rerun_batch["parent_batch_id"])

    assert derived_run["parent_run_id"] == run["run_id"]
    assert cloned_parent["run_id"] == derived_run["run_id"]
    assert cloned_parent["batch_id"] != original_parent["batch_id"]
    assert derived_jobs_dir == original_jobs_dir
    assert derived_jobs_dir != derived_jobs_dir_for_run(store=store, run=derived_run)
    assert original_ok_trial.exists()
    assert original_exc_trial.exists()
    final_job_dir = derived_jobs_dir / sanitize_name(str(derived_run["display_name"]))
    assert final_job_dir.name == f"{original_job_dir.name}-rerun-{derived_run['run_id']}"
    assert (final_job_dir / "ok__old" / "result.json").exists()
    assert (final_job_dir / "exc-a__old" / "result.json").exists()

    rerun_imported_dir = store.layout.controller_dir / "imported-jobs" / rerun_batch["batch_id"]
    rerun_result_trial = _write_jobs_trial(rerun_imported_dir, "exc-a__new", task_name="exc-a")

    server = start_test_server(store, tmp_path, 9898)
    conn = HTTPConnection("127.0.0.1", 9898)
    body = json.dumps(
        {
            "batchId": rerun_batch["batch_id"],
            "workerId": "worker-a",
            "status": "succeeded",
            "finished": True,
            "executorMetadata": {"combinedJobsDir": str(derived_jobs_dir)},
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
    with patch("agent_eval_orchestrator.normalizers.harbor_job_merge.finalize_job_result_with_harbor"):
        conn.request(
            "POST",
            "/api/workers/heartbeat",
            body=body,
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert payload["batch"]["batch_id"] == rerun_batch["batch_id"]
    original_cases = {
        case["case_id"]: case for case in store.list_case_runs(original_parent["batch_id"])
    }
    assert original_cases["ok"]["status"] == "succeeded"
    assert original_cases["exc-a"]["status"] == "errored"
    cloned_cases = {
        case["case_id"]: case for case in store.list_case_runs(cloned_parent["batch_id"])
    }
    assert cloned_cases["ok"]["status"] == "succeeded"
    assert cloned_cases["exc-a"]["status"] == "succeeded"
    assert store.get_run(derived_run["run_id"])["rerun_status"] == "succeeded"
    assert store.get_run_rerun_job(job["job_id"])["status"] == "succeeded"
    assert store.list_case_runs(rerun_batch["batch_id"]) == []
    assert (final_job_dir / "config.json").exists()
    assert (final_job_dir / "ok__old" / "result.json").exists()
    assert (final_job_dir / "exc-a__new" / "result.json").exists()
    assert not (final_job_dir / "exc-a__old").exists()
    assert not (final_job_dir / "other__old").exists()
    assert original_ok_trial.exists()
    assert original_exc_trial.exists()
    assert rerun_result_trial.exists()
    server.shutdown()


def test_job_archive_for_derived_exception_rerun_does_not_rebuild_from_sibling_jobs(store, tmp_path):
    run, _original_parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-a", "status": "errored", "error_text": "ValueError: boom"},
        ],
    )
    original_jobs_dir = tmp_path / "original-harbor" / "jobs"
    original_job_dir = original_jobs_dir / sanitize_name(str(run["display_name"]))
    (original_job_dir / "config.json").parent.mkdir(parents=True)
    (original_job_dir / "config.json").write_text(
        json.dumps({"job_name": original_job_dir.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    _write_jobs_trial(original_job_dir, "ok__old", task_name="ok")
    original_exc_trial = _write_jobs_trial(original_job_dir, "exc-a__old", task_name="exc-a")
    (original_exc_trial / "exception.txt").write_text(
        "Traceback (most recent call last):\nValueError: boom\n",
        encoding="utf-8",
    )
    sibling_job_dir = original_jobs_dir / "sibling-job"
    _write_jobs_trial(sibling_job_dir, "other__old", task_name="other")
    (sibling_job_dir / "config.json").write_text(
        json.dumps({"job_name": sibling_job_dir.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    store.update_task_template_executor_config(
        str(run["template_id"]),
        {"combinedJobsDir": str(original_jobs_dir)},
    )
    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(run["run_id"])
    derived_run = store.get_run(result["runId"])
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    final_job_dir = original_jobs_dir / sanitize_name(str(derived_run["display_name"]))

    archive_root = tmp_path / "archive"
    archived_batch_dir = archive_root / rerun_batch["batch_id"]
    _write_jobs_trial(archived_batch_dir, "exc-a__new", task_name="exc-a")

    server = start_test_server(store, tmp_path, 9902)
    conn = HTTPConnection("127.0.0.1", 9902)
    body = json.dumps(
        {
            "batchId": rerun_batch["batch_id"],
            "jobsDir": str(original_jobs_dir),
            "archiveBase64": _tar_dir_base64(archived_batch_dir),
        }
    )
    with patch("agent_eval_orchestrator.normalizers.harbor_job_merge.finalize_job_result_with_harbor"):
        conn.request(
            "POST",
            "/api/workers/job-archive",
            body=body,
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()

    assert resp.status == 200
    assert (final_job_dir / "ok__old" / "result.json").exists()
    assert (final_job_dir / "exc-a__new" / "result.json").exists()
    assert not (final_job_dir / "exc-a__old").exists()
    assert not (final_job_dir / "other__old").exists()
    trial_dirs = [child for child in final_job_dir.iterdir() if child.is_dir()]
    assert sorted(child.name for child in trial_dirs) == ["exc-a__new", "ok__old"]
    server.shutdown()


def test_job_archive_imports_primary_job_archive_by_batch_id(store, tmp_path):
    run, batch = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "case-a", "status": "failed", "score": 0.0}],
    )
    jobs_dir = tmp_path / "harbor" / "jobs"
    store.update_task_template_executor_config(
        str(run["template_id"]),
        {"combinedJobsDir": str(jobs_dir)},
    )

    archive_root = tmp_path / "archive"
    archived_job_dir = archive_root / "worker-job-name"
    _write_jobs_trial(archived_job_dir, "case-a__new", task_name="case-a")
    (archived_job_dir / "config.json").write_text(
        json.dumps({"job_name": archived_job_dir.name, "jobs_dir": str(archive_root)}),
        encoding="utf-8",
    )

    server = start_test_server(store, tmp_path, 9903)
    conn = HTTPConnection("127.0.0.1", 9903)
    body = json.dumps(
        {
            "batchId": batch["batch_id"],
            "jobsDir": str(jobs_dir),
            "archiveBase64": _tar_dir_base64(archived_job_dir),
        }
    )
    with patch("agent_eval_orchestrator.normalizers.harbor_job_merge.finalize_job_result_with_harbor"):
        conn.request(
            "POST",
            "/api/workers/job-archive",
            body=body,
            headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
        )
        resp = conn.getresponse()

    assert resp.status == 200
    imported_job_dir = store.layout.controller_dir / "imported-jobs" / batch["batch_id"]
    merged_job_dir = jobs_dir / sanitize_name(str(run["display_name"]))
    assert (imported_job_dir / "case-a__new" / "result.json").exists()
    assert (merged_job_dir / "case-a__new" / "result.json").exists()
    assert (merged_job_dir / "config.json").exists()
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
