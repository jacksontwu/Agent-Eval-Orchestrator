from agent_eval_orchestrator.core.ids import new_id


def _sample_steps():
    return [
        {"id": "validate_ssh", "label": "校验 SSH 连接", "status": "pending"},
        {"id": "stop_daemon", "label": "停止 Worker Daemon", "status": "pending"},
    ]


def test_worker_update_job_crud(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    job = store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo", "harbor"],
        steps=_sample_steps(),
    )
    assert job["status"] == "pending"
    assert job["targets"] == ["aeo", "harbor"]

    store.append_worker_update_log(job_id, "pull output\n")
    updated = store.update_worker_update_job(
        job_id,
        status="running",
        current_step="validate_ssh",
        steps=[{"id": "validate_ssh", "label": "校验 SSH 连接", "status": "running"}],
    )
    assert updated is not None
    assert updated["log_text"].endswith("pull output\n")

    fetched = store.get_worker_update_job(job_id)
    assert fetched is not None
    assert fetched["worker_id"] == "ecs-worker-upd"


def test_get_active_worker_update_job(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=_sample_steps(),
    )
    active = store.get_active_worker_update_job_for_worker("ecs-worker-upd")
    assert active is not None
    assert active["job_id"] == job_id

    store.update_worker_update_job(job_id, status="succeeded", finished=True)
    assert store.get_active_worker_update_job_for_worker("ecs-worker-upd") is None


def test_delete_worker_removes_update_jobs(store):
    store.create_provisioning_worker(
        worker_id="ecs-worker-upd",
        display_name="ecs-worker-upd",
        slots_total=1,
        ssh_host_alias="aeo-ecs-0004",
        ssh_bootstrap_host_alias=None,
        tunnel_remote_port=17380,
    )
    job_id = new_id("upd")
    store.create_worker_update_job(
        job_id=job_id,
        worker_id="ecs-worker-upd",
        targets=["aeo"],
        steps=_sample_steps(),
    )
    assert store.delete_worker("ecs-worker-upd") is True
    assert store.get_worker_update_job(job_id) is None
