import pytest

from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator, RerunValidationError
from conftest import seed_finished_run_with_cases


@pytest.fixture()
def coordinator(store):
    return RunRerunCoordinator(store=store, asset_syncer=None)


def test_start_rerun_rejects_unfinished_run(coordinator, store):
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
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])
    assert exc.value.code == 409
    assert "not finished" in exc.value.message.lower()


def test_start_rerun_creates_batches_and_job(coordinator, store):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    result = coordinator.start_rerun(run["run_id"])
    assert result["exceptionCount"] == 1
    assert result["rerunStatus"] == "syncing"
    updated = store.get_run(run["run_id"])
    assert updated["rerun_status"] == "syncing"
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job is not None
    assert job["rerun_batches"]["worker-a"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_kind"] == "exception_rerun"
    assert rerun_batch["parent_batch_id"] == parent["batch_id"]
    assert rerun_batch["status"] == "pending_sync"
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_start_rerun_rejects_no_exceptions(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])
    assert exc.value.code == 400
