import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from agent_eval_orchestrator.controller.provisioner import Provisioner
from agent_eval_orchestrator.controller.server import Handler, ThreadedServer
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
    server = ThreadedServer(("127.0.0.1", port), Handler)
    Handler.store = store
    Handler.auth_token = "secret"
    Handler.viewer_manager = None
    Handler.provisioner = provisioner
    Handler.ssh_config_path = ssh_config
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def delete_worker(port: int, worker_id: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port)
    conn.request(
        "DELETE",
        f"/api/workers/{worker_id}",
        headers={"X-AEO-Token": "secret"},
    )
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    return resp.status, body


def _seed_worker(store, worker_id: str = "ecs-worker-del", *, ssh_alias: str | None = None):
    if ssh_alias:
        store.create_provisioning_worker(
            worker_id=worker_id,
            display_name=worker_id,
            slots_total=1,
            ssh_host_alias=ssh_alias,
            ssh_bootstrap_host_alias=None,
            tunnel_remote_port=17380,
        )
        store.set_worker_provision_status(worker_id, provision_status="ready")
    else:
        store.register_worker(
            worker_id=worker_id,
            display_name=worker_id,
            host="10.0.0.1",
            slots_total=1,
            slots_used=0,
            capabilities={},
        )


def _seed_template(store):
    return store.create_task_template(
        owner="default",
        name="delete-api-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )


def test_delete_worker_not_found(store, sample_ssh_config):
    server = start_test_server(store, sample_ssh_config, 9878)
    status, body = delete_worker(9878, "missing-worker")
    assert status == 404
    assert body == {"error": "worker not found"}
    server.shutdown()


def test_delete_worker_with_running_batch(store, sample_ssh_config):
    _seed_worker(store)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET status = 'running', assigned_worker_id = ?, current_step = 'executor-starting'
            WHERE batch_id = ?
            """,
            ("ecs-worker-del", batch["batch_id"]),
        )
    server = start_test_server(store, sample_ssh_config, 9879)
    status, body = delete_worker(9879, "ecs-worker-del")
    assert status == 409
    assert body["error"] == "worker has active batches"
    assert body["runningCount"] == 1
    assert body["queuedCount"] == 0
    server.shutdown()


def test_delete_worker_with_queued_batch(store, sample_ssh_config):
    _seed_worker(store)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    server = start_test_server(store, sample_ssh_config, 9880)
    status, body = delete_worker(9880, "ecs-worker-del")
    assert status == 409
    assert body["runningCount"] == 0
    assert body["queuedCount"] == 1
    server.shutdown()


def test_delete_worker_success_no_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    server = start_test_server(store, sample_ssh_config, 9881)
    status, body = delete_worker(9881, "ecs-worker-del")
    assert status == 200
    assert body == {
        "ok": True,
        "workerId": "ecs-worker-del",
        "remoteCleanup": "skipped",
    }
    assert store.worker_exists("ecs-worker-del") is False
    server.shutdown()


def test_delete_worker_success_with_ssh(store, sample_ssh_config):
    _seed_worker(store, ssh_alias="aeo-ecs-0004")
    server = start_test_server(store, sample_ssh_config, 9882)
    with patch.object(Handler.provisioner, "decommission_worker", return_value={"remoteCleanup": "done", "warnings": []}):
        status, body = delete_worker(9882, "ecs-worker-del")
    assert status == 200
    assert body["remoteCleanup"] == "done"
    assert store.worker_exists("ecs-worker-del") is False
    server.shutdown()


def test_delete_worker_cancels_provision_job(store, sample_ssh_config):
    store.create_provisioning_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("prov")
    store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-del",
        mode="join",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"}],
    )
    store.update_provision_job(job_id, status="running")
    server = start_test_server(store, sample_ssh_config, 9883)
    with patch.object(
        Handler.provisioner,
        "decommission_worker",
        return_value={"remoteCleanup": "done", "warnings": []},
    ):
        status, body = delete_worker(9883, "ecs-worker-del")
    assert status == 200
    cancelled = store.get_provision_job(job_id)
    assert cancelled is None
    server.shutdown()


def test_delete_worker_id_reusable(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    server = start_test_server(store, sample_ssh_config, 9884)
    delete_worker(9884, "ecs-worker-del")
    server.shutdown()
    assert store.worker_exists("ecs-worker-del") is False
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.2",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.worker_exists("ecs-worker-del") is True


def test_historical_batch_keeps_worker_id(store, sample_ssh_config):
    _seed_worker(store, ssh_alias=None)
    template = _seed_template(store)
    run = store.create_run(template_id=template["template_id"])
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE batches
            SET status = 'finished', assigned_worker_id = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            ("ecs-worker-del", "2026-05-24T00:00:00+00:00", batch["batch_id"]),
        )
    server = start_test_server(store, sample_ssh_config, 9885)
    delete_worker(9885, "ecs-worker-del")
    server.shutdown()
    detail = store.get_batch_detail(batch["batch_id"])
    assert detail is not None
    assert detail["batch"]["assigned_worker_id"] == "ecs-worker-del"
    assert detail["worker"] is None
