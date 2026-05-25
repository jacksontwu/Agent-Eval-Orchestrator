import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
from agent_eval_orchestrator.controller.worker_updater import WorkerUpdater
from agent_eval_orchestrator.core.ids import new_id
from agent_eval_orchestrator.storage.store import Store


def start_test_server(store: Store, ssh_config: Path, port: int) -> ThreadedServer:
    bootstrap = ssh_config.parent / "bootstrap.sh"
    bootstrap.write_text("#!/bin/bash\n", encoding="utf-8")
    provisioner = Provisioner(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        bootstrap_script_path=bootstrap,
        tunnel_state_path=store.layout.controller_dir / "tunnels.json",
    )
    worker_updater = WorkerUpdater(
        store=store,
        ssh_config_path=ssh_config,
        auth_token="secret",
        controller_port=port,
        provisioner=provisioner,
    )
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.provisioner = provisioner
    Handler.worker_updater = worker_updater
    Handler.ssh_config_path = ssh_config
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def post_update(port: int, worker_id: str, body: dict | None = None) -> tuple[int, dict]:
    payload = json.dumps(body or {}).encode("utf-8")
    conn = HTTPConnection("127.0.0.1", port)
    conn.request(
        "POST",
        f"/api/workers/{worker_id}/update",
        body=payload,
        headers={"Content-Type": "application/json", "X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode("utf-8"))


def _seed_worker(store, *, ssh: bool = True):
    if ssh:
        store.create_provisioning_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            slots_total=1,
            ssh_host_alias="aeo-ecs-0004",
            ssh_bootstrap_host_alias=None,
            connection_mode="direct",
            controller_internal_ip="192.168.0.211",
            tunnel_remote_port=None,
        )
        store.set_worker_provision_status("ecs-worker-upd", provision_status="ready")
        store.register_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
        )
    else:
        store.register_worker(
            worker_id="ecs-worker-upd",
            display_name="ecs-worker-upd",
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={},
        )


def test_update_worker_not_found(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9881)
    status, body = post_update(9881, "missing")
    assert status == 404
    assert body == {"error": "worker not found"}
    server.shutdown()


def test_update_worker_no_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh=False)
    server = start_test_server(store, sample_ssh_config, 9882)
    status, body = post_update(9882, "ecs-worker-upd")
    assert status == 400
    assert body == {"error": "ssh_host_alias required"}
    server.shutdown()


def test_update_worker_active_batches(store, sample_ssh_config):
    _seed_worker(store)
    template = store.create_task_template(
        owner="default",
        name="upd-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-upd",
        batch_options={},
    )
    server = start_test_server(store, sample_ssh_config, 9883)
    status, body = post_update(9883, "ecs-worker-upd")
    assert status == 409
    assert body["error"] == "worker has active batches"
    server.shutdown()


def test_update_worker_starts_job(store, sample_ssh_config):
    _seed_worker(store)
    server = start_test_server(store, sample_ssh_config, 9884)
    with patch.object(WorkerUpdater, "start_job_async") as mock_start:
        status, body = post_update(9884, "ecs-worker-upd", {"targets": ["aeo"]})
    assert status == 202
    assert body["workerId"] == "ecs-worker-upd"
    assert body["targets"] == ["aeo"]
    assert body["jobId"].startswith("upd-")
    mock_start.assert_called_once()
    server.shutdown()


def test_update_worker_allows_stale_running_provision_job_when_ready(store, sample_ssh_config):
    _seed_worker(store)
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        mode="join",
        steps=[{"id": "start_daemon", "label": "启动", "status": "running"}],
    )
    store.update_provision_job(job_id, status="running", current_step="start_daemon")
    server = start_test_server(store, sample_ssh_config, 9885)
    with patch.object(WorkerUpdater, "start_job_async") as mock_start:
        status, body = post_update(9885, "ecs-worker-upd", {"targets": ["harbor"]})
    assert status == 202
    assert body["targets"] == ["harbor"]
    mock_start.assert_called_once()
    server.shutdown()


def test_update_worker_blocks_active_provisioning(store, sample_ssh_config):
    _seed_worker(store)
    store.set_worker_provision_status("ecs-worker-upd", provision_status="provisioning")
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验", "status": "running"}],
    )
    store.update_provision_job(job_id, status="running", current_step="validate_ssh")
    server = start_test_server(store, sample_ssh_config, 9886)
    status, body = post_update(9886, "ecs-worker-upd")
    assert status == 409
    assert body == {"error": "provision in progress"}
    server.shutdown()
