import os

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


def test_start_rerun_applies_config_and_updates_template_and_manifest(store, tmp_path):
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
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {
                "nConcurrent": 3,
                "timeoutMultiplier": 1.4,
                "agentTimeoutMultiplier": 3.4,
                "verifierTimeoutMultiplier": 2.4,
                "environmentBuildTimeoutMultiplier": 1.8,
            },
        },
    )

    assert result["exceptionCount"] == 1
    template = store.get_task_template(run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    executor_config = template["executor_config"]
    assert executor_config["nConcurrent"] == 3
    assert executor_config["timeoutMultiplier"] == 1.4
    assert executor_config["agentTimeoutMultiplier"] == 3.4
    assert executor_config["verifierTimeoutMultiplier"] == 2.4
    assert executor_config["environmentBuildTimeoutMultiplier"] == 1.8
    assert executor_config["combinedJobsDir"] == assets["jobsDir"]
    updated_run = store.get_run(run["run_id"])
    manifest = updated_run["sync_manifest"]
    assert manifest["datasetPath"] == assets["datasetPath"]
    assert manifest["bitfunCliPath"] == assets["bitfunCliPath"]
    assert manifest["bitfunConfigDir"] == assets["bitfunConfigDir"]
    assert manifest["workers"]["worker-a"]["targetRoot"] == previous_target
    assert manifest["workers"]["worker-a"]["transport"] == "local"
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_options"]["concurrency"] == 3


def test_start_rerun_config_validation_happens_before_job_creation(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, [])
    original_template = store.get_task_template(run["template_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": {"nConcurrent": 2},
            },
        )

    assert exc.value.code == 400
    assert "case directory not found: exc-a" in exc.value.message
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert store.get_task_template(run["template_id"])["dataset_ref"] == original_template["dataset_ref"]


def test_start_rerun_config_replaces_stale_executor_worker_maps(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    _make_worker_local(store, tmp_path)
    store.update_task_template_executor_config(
        run["template_id"],
        {
            "useAssetSync": True,
            "uvBinaryByWorker": {
                "worker-a": "/old/uv-a",
                "worker-b": "/old/uv-b",
            },
            "harborRepoPathByWorker": {
                "worker-a": "/old/harbor-a",
                "worker-b": "/old/harbor-b",
            },
            "datasetPathByWorker": {
                "worker-a": "/old/dataset-a",
                "worker-b": "/old/dataset-b",
            },
            "mountsByWorker": {
                "worker-a": [{"source": "/old/source-a"}],
                "worker-b": [{"source": "/old/source-b"}],
            },
        },
    )
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        },
    )

    executor_config = store.get_task_template(run["template_id"])["executor_config"]
    for key in (
        "uvBinaryByWorker",
        "harborRepoPathByWorker",
        "datasetPathByWorker",
        "mountsByWorker",
    ):
        assert "worker-b" not in executor_config[key]
