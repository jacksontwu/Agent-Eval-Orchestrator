from agent_eval_orchestrator.core.ids import new_id


def _seed_template_and_worker(store, worker_id: str = "ecs-worker-del"):
    store.register_worker(
        worker_id=worker_id,
        display_name=worker_id,
        host="10.0.0.1",
        slots_total=2,
        slots_used=0,
        capabilities={},
    )
    template = store.create_task_template(
        owner="default",
        name="delete-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor",
        executor_config={"jobsDir": "/tmp/jobs"},
        model_profile_ref=None,
        note="",
    )
    return template


def test_worker_has_active_batches_empty(store):
    _seed_template_and_worker(store)
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 0}


def test_worker_has_active_batches_queued(store):
    template = _seed_template_and_worker(store)
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="ecs-worker-del",
        batch_options={},
    )
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 1}


def test_worker_has_active_batches_running(store):
    template = _seed_template_and_worker(store)
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
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 1, "queuedCount": 0}


def test_worker_has_active_batches_uses_bound_worker_id(store):
    template = _seed_template_and_worker(store)
    run = store.create_run(template_id=template["template_id"])
    with store.connect() as conn:
        conn.execute(
            "UPDATE runs SET bound_worker_id = ? WHERE run_id = ?",
            ("ecs-worker-del", run["run_id"]),
        )
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id=None,
        batch_options={},
    )
    counts = store.worker_has_active_batches("ecs-worker-del")
    assert counts == {"runningCount": 0, "queuedCount": 1}


def test_delete_worker_not_found(store):
    assert store.delete_worker("missing-worker") is False


def test_delete_worker_removes_row_and_provision_jobs(store):
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
    assert store.delete_worker("ecs-worker-del") is True
    assert store.worker_exists("ecs-worker-del") is False
    assert store.get_provision_job(job_id) is None
    assert store.list_workers() == []


def test_delete_worker_id_reusable(store):
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.1",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.delete_worker("ecs-worker-del") is True
    store.register_worker(
        worker_id="ecs-worker-del",
        display_name="ecs-worker-del",
        host="10.0.0.2",
        slots_total=1,
        slots_used=0,
        capabilities={},
    )
    assert store.worker_exists("ecs-worker-del") is True
