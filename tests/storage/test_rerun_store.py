from agent_eval_orchestrator.core.ids import new_id

from conftest import seed_finished_run_with_cases


def test_rerun_schema_and_crud(store):
    template = store.create_task_template(
        owner="default",
        name="rerun-test",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="rerun-run")
    job_id = new_id("rerun")

    store.update_run_rerun_fields(
        run_id=run["run_id"],
        rerun_status="syncing",
        rerun_job_id=job_id,
    )
    updated_run = store.get_run(run["run_id"])
    assert updated_run["rerun_status"] == "syncing"
    assert updated_run["rerun_job_id"] == job_id

    job = store.create_run_rerun_job(
        job_id=job_id,
        run_id=run["run_id"],
        case_ids=["case-a", "case-b"],
        worker_shards={"worker-a": ["case-a"], "worker-b": ["case-b"]},
        rerun_batches={"worker-a": "batch-rerun-a", "worker-b": "batch-rerun-b"},
    )
    assert job["status"] == "pending"
    assert job["case_ids"] == ["case-a", "case-b"]
    assert job["worker_shards"]["worker-a"] == ["case-a"]

    store.update_run_rerun_job(job_id, status="running", sync_job_id="sync-1")
    fetched = store.get_run_rerun_job(job_id)
    assert fetched["status"] == "running"
    assert fetched["sync_job_id"] == "sync-1"

    active = store.get_active_run_rerun_job(run["run_id"])
    assert active is not None
    assert active["job_id"] == job_id


def _seed_finished_run_with_cases(store, *, cases):
    return seed_finished_run_with_cases(store, cases=cases)


def test_list_exception_cases_for_run(store):
    run, batch = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "fail", "status": "failed", "score": 0.0},
            {"case_id": "exc", "status": "errored", "error_text": "boom"},
            {"case_id": "legacy", "status": "failed", "error_text": "timeout"},
        ],
    )
    exceptions = store.list_exception_cases_for_run(run["run_id"])
    case_ids = sorted(item["case_id"] for item in exceptions)
    assert case_ids == ["exc", "legacy"]
    assert all(item["parent_batch_id"] == batch["batch_id"] for item in exceptions)
    assert all(item["worker_id"] == "worker-a" for item in exceptions)


def test_is_run_primary_terminal_ignores_active_rerun_batches(store):
    run, batch = _seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc", "status": "errored", "error_text": "boom"}],
    )
    assert store.is_run_primary_terminal(run["run_id"]) is True
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc"],
        preferred_worker_id="worker-a",
        batch_options={},
        initial_status="running",
        batch_kind="exception_rerun",
        parent_batch_id=batch["batch_id"],
    )
    assert store.is_run_primary_terminal(run["run_id"]) is True
    assert store.is_run_terminal(run["run_id"]) is False


def test_merge_rerun_cases_into_parent_overwrites_exceptions_only(store):
    run, parent = _seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
            {"case_id": "exc", "status": "errored", "error_text": "boom"},
        ],
    )
    rerun = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc"],
        preferred_worker_id="worker-a",
        batch_options={},
        batch_kind="exception_rerun",
        parent_batch_id=parent["batch_id"],
    )
    merged = store.merge_rerun_cases_into_parent(
        parent_batch_id=parent["batch_id"],
        rerun_cases=[
            {
                "caseId": "exc",
                "status": "succeeded",
                "score": 1.0,
                "metrics": {},
                "artifactIndex": {},
            }
        ],
        rerun_batch_id=rerun["batch_id"],
    )
    assert merged is not None
    parent_cases = store.list_case_runs(parent["batch_id"])
    by_id = {case["case_id"]: case for case in parent_cases}
    assert by_id["ok"]["status"] == "succeeded"
    assert by_id["exc"]["status"] == "succeeded"
    assert by_id["exc"]["score"] == 1.0
    assert merged["summary"]["succeeded"] == 2
    assert merged["summary"]["errored"] == 0
    rerun_cases = store.list_case_runs(rerun["batch_id"])
    assert rerun_cases == []


def test_eval_task_detail_includes_rerun_fields(store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="idle")
    detail = store.get_eval_task_detail(run["run_id"])
    assert detail["canRerunExceptions"] is True
    assert detail["run"]["rerun_status"] == "idle"
    assert detail["batches"][0]["batch_kind"] == "primary"
