from agent_eval_orchestrator.core.ids import new_id, now_iso


def test_provision_schema_and_crud(store):
    worker = store.create_provisioning_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias="aeo-ecs-0004-root",
        tunnel_remote_port=17380,
    )
    assert worker["provision_status"] == "provisioning"
    assert worker["ssh_host_alias"] == "aeo-ecs-0004"

    job_id = new_id("prov")
    job = store.create_provision_job(
        job_id=job_id,
        worker_id="ecs-worker-0004",
        mode="fresh",
        steps=[
            {"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"},
        ],
    )
    assert job["status"] == "pending"

    store.append_provision_log(job_id, "line one\n")
    updated = store.update_provision_job(
        job_id,
        status="running",
        current_step="validate_ssh",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "running"}],
    )
    assert updated["log_text"].endswith("line one\n")
    assert updated["status"] == "running"

    fetched = store.get_provision_job(job_id)
    assert fetched is not None
    assert fetched["worker_id"] == "ecs-worker-0004"


def test_register_worker_marks_provision_ready(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    worker = store.register_worker(
        worker_id="ecs-worker-0004",
        display_name="ecs-worker-0004",
        host="worker-host",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/home/djn/worker/agent-eval-orchestrator/runtime"},
    )
    assert worker["provision_status"] == "ready"
    assert worker["status"] == "online"
