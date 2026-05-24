import json

from agent_eval_orchestrator.core.ids import new_id


def test_asset_sync_schema_and_crud(store):
    template = store.create_task_template(
        owner="default",
        name="sync-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="sync-run")
    job_id = new_id("sync")

    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="pending",
        sync_job_id=job_id,
        sync_manifest={"datasetPath": "/tmp/dataset", "workers": {}},
    )
    updated_run = store.get_run(run["run_id"])
    assert updated_run["sync_status"] == "pending"
    assert updated_run["sync_job_id"] == job_id
    assert updated_run["sync_manifest"]["datasetPath"] == "/tmp/dataset"

    job = store.create_asset_sync_job(
        job_id=job_id,
        run_id=run["run_id"],
        steps=[
            {
                "workerId": "local-a",
                "steps": [
                    {"id": "sync_cases", "label": "同步 dataset case", "status": "pending"},
                    {"id": "sync_bitfun", "label": "同步 bitfun-cli", "status": "pending"},
                ],
            }
        ],
    )
    assert job["status"] == "pending"
    assert job["steps"][0]["workerId"] == "local-a"

    store.append_asset_sync_log(job_id, "line one\n")
    updated = store.update_asset_sync_job(
        job_id,
        status="running",
        current_step="sync_cases",
        steps=[
            {
                "workerId": "local-a",
                "steps": [
                    {"id": "sync_cases", "label": "同步 dataset case", "status": "running"},
                    {"id": "sync_bitfun", "label": "同步 bitfun-cli", "status": "pending"},
                ],
            }
        ],
    )
    assert updated["log_text"].endswith("line one\n")
    assert updated["status"] == "running"

    fetched = store.get_asset_sync_job(job_id)
    assert fetched is not None
    assert fetched["run_id"] == run["run_id"]
    assert fetched["log_tail"].endswith("line one\n")


def test_pending_sync_batches_and_promotion(store):
    template = store.create_task_template(
        owner="default",
        name="batch-sync",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
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
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="pending_sync",
    )
    assert batch["status"] == "pending_sync"

    claimed = store.claim_next_batch("worker-a")
    assert claimed is None

    store.promote_worker_batches_to_queued(run_id=run["run_id"], worker_id="worker-a")
    claimed = store.claim_next_batch("worker-a")
    assert claimed is not None
    assert claimed["batch"]["batch_id"] == batch["batch_id"]


def test_update_task_template_executor_config(store):
    template = store.create_task_template(
        owner="default",
        name="cfg",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True, "datasetPathByWorker": {}},
        model_profile_ref=None,
        note="",
    )
    updated = store.update_task_template_executor_config(
        template["template_id"],
        {
            "datasetPathByWorker": {"worker-a": "/sync/run-1/dataset"},
            "mountsByWorker": {
                "worker-a": [
                    {"type": "bind", "source": "/sync/run-1/bitfun/bitfun-cli", "target": "/usr/local/bin/bitfun-cli"},
                ]
            },
        },
    )
    assert updated["executor_config"]["datasetPathByWorker"]["worker-a"] == "/sync/run-1/dataset"
    assert updated["executor_config"]["mountsByWorker"]["worker-a"][0]["source"].endswith("bitfun-cli")


def test_is_run_terminal(store):
    template = store.create_task_template(
        owner="default",
        name="term",
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
    batch = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    assert store.is_run_terminal(run["run_id"]) is False
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
    )
    assert store.is_run_terminal(run["run_id"]) is True


def test_eval_task_summary_includes_sync_status(store):
    template = store.create_task_template(
        owner="default",
        name="summary",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.update_run_sync_fields(run_id=run["run_id"], sync_status="running")
    summaries = store.list_eval_task_summaries()
    match = next(item for item in summaries if item["runId"] == run["run_id"])
    assert match["syncStatus"] == "running"
