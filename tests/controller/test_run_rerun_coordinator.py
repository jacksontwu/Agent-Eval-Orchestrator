import json
import os
from pathlib import Path
from threading import Event, Lock, Thread

import pytest
import yaml

import agent_eval_orchestrator.controller.run_rerun_coordinator as rerun_coordinator_module
from agent_eval_orchestrator.controller.rerun_artifacts import (
    derived_jobs_dir_for_run,
    derived_rerun_job_name,
)
from agent_eval_orchestrator.controller.run_rerun_coordinator import RunRerunCoordinator, RerunValidationError
from agent_eval_orchestrator.core.ids import sanitize_name
from conftest import seed_finished_run_with_cases


@pytest.fixture()
def coordinator(store):
    return RunRerunCoordinator(store=store, asset_syncer=None)


def _derived_runs_for_parent(store, parent_run_id):
    return [
        item for item in store.list_runs()
        if item.get("parent_run_id") == parent_run_id
    ]


def _make_dataset(tmp_path, case_ids):
    dataset = tmp_path / "dataset"
    for case_id in case_ids:
        case_dir = dataset / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "task.toml").write_text("", encoding="utf-8")
    return dataset


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
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    result = coordinator.start_rerun(run["run_id"])
    assert result["exceptionCount"] == 1
    assert result["rerunStatus"] == "syncing"
    updated = store.get_run(result["runId"])
    assert updated["rerun_status"] == "syncing"
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job is not None
    assert job["run_id"] == result["runId"]
    assert job["rerun_batches"]["worker-a"]
    derived_primary = [
        batch for batch in store.list_batches_for_run(result["runId"])
        if batch["batch_kind"] == "primary"
    ]
    assert len(derived_primary) == 1
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_kind"] == "exception_rerun"
    assert rerun_batch["parent_batch_id"] == derived_primary[0]["batch_id"]
    assert rerun_batch["status"] == "pending_sync"
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_preview_harbor_yaml_returns_original_yaml_and_scope_stats(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "exc-b", "ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "timeout", "metrics": {"errorType": "AgentTimeoutError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    yaml_text = f"""
job_name: original
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - ok
"""
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    store.update_task_template_executor_config(
        run["template_id"],
        {
            "harborYaml": yaml_text,
            "harborYamlMode": "datasets",
            "harborYamlTaskIds": ["exc-a", "exc-b", "ok"],
            "combinedJobsDir": "",
        },
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    preview = coordinator.preview_harbor_yaml(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"]},
    )

    assert preview["source"] == "original_yaml"
    assert preview["harborYaml"].strip() == yaml_text.strip()
    assert preview["exceptionCount"] == 1
    assert preview["selectedErrorTypes"] == ["stderr"]
    assert preview["workerShards"] == {"worker-a": 1}


def test_preview_harbor_yaml_generates_legacy_yaml(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    store.update_task_template_executor_config(
        run["template_id"],
        {
            "agentName": "bitfun-cli",
            "modelName": "deepseek-v4-pro",
            "nConcurrent": 6,
            "timeoutMultiplier": 1.25,
            "agentTimeoutMultiplier": 3,
            "verifierTimeoutMultiplier": 2,
            "environmentBuildTimeoutMultiplier": 1.5,
            "envType": "docker",
        },
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    preview = coordinator.preview_harbor_yaml(run["run_id"], config={"selectedErrorTypes": ["stderr"]})
    payload = yaml.safe_load(preview["harborYaml"])

    assert preview["source"] == "generated_legacy_yaml"
    assert payload["n_concurrent_trials"] == 6
    assert payload["timeout_multiplier"] == 1.25
    assert payload["agent_timeout_multiplier"] == 3
    assert payload["verifier_timeout_multiplier"] == 2
    assert payload["environment_build_timeout_multiplier"] == 1.5
    assert payload["agents"] == [{"name": "bitfun-cli", "model_name": "deepseek-v4-pro"}]
    assert payload["environment"] == {"type": "docker"}
    assert payload["datasets"][0]["path"] == str(dataset)
    assert payload["datasets"][0]["task_names"] == ["exc-a", "ok"]


def test_start_rerun_harbor_yaml_ignores_submitted_task_names_and_writes_batch_yaml(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "exc-b", "ok"])
    _make_worker_local(store, tmp_path)
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "timeout", "metrics": {"errorType": "AgentTimeoutError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    submitted_yaml = f"""
job_name: user-edited-job
jobs_dir: user-jobs
n_concurrent_trials: 9
agents:
  - name: codex
    model_name: openai/gpt-4o
datasets:
  - path: {dataset}
    task_names:
      - ok
"""

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
    )

    derived_run = store.get_run(result["runId"])
    derived_template = store.get_task_template(derived_run["template_id"])
    executor_config = derived_template["executor_config"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    batch_yaml = yaml.safe_load(executor_config["harborYamlByBatchId"][rerun_batch["batch_id"]])

    assert result["exceptionCount"] == 1
    assert rerun_batch["selected_case_ids"] == ["exc-a"]
    assert executor_config["harborYaml"] == submitted_yaml.strip()
    assert executor_config["harborYamlMode"] == "datasets"
    assert executor_config["harborYamlTaskIds"] == ["exc-a"]
    assert executor_config["harborYamlGeneratedJobName"] == sanitize_name(derived_run["display_name"])
    assert executor_config["combinedJobsDir"] == str(derived_jobs_dir_for_run(store=store, run=derived_run))
    assert batch_yaml["n_concurrent_trials"] == 9
    assert batch_yaml["agents"][0]["name"] == "codex"
    assert batch_yaml["datasets"][0]["task_names"] == ["exc-a"]
    assert batch_yaml["job_name"] == sanitize_name(derived_run["display_name"])
    assert batch_yaml["jobs_dir"] == str(Path(rerun_batch["batch_root"]) / "harbor" / "jobs")


def test_start_rerun_harbor_yaml_rejects_changed_dataset_missing_selected_case(store, tmp_path):
    source_dataset = _make_dataset(tmp_path / "source", ["exc-a", "ok"])
    target_dataset = _make_dataset(tmp_path / "target", ["ok"])
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(source_dataset))
    submitted_yaml = f"""
agents:
  - name: codex
datasets:
  - path: {target_dataset}
    task_names:
      - ok
"""

    with pytest.raises(RerunValidationError) as exc:
        RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
            run["run_id"],
            config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
        )

    assert exc.value.code == 400
    assert exc.value.message == "case directory not found: exc-a"
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_harbor_yaml_manifest_includes_bind_assets(store, tmp_path):
    dataset = _make_dataset(tmp_path, ["exc-a", "ok"])
    codeagent = tmp_path / "codeagentcli"
    codeagent.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(codeagent, 0o755)
    _make_worker_local(store, tmp_path)
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "stderr"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    store.update_task_template_dataset_ref(run["template_id"], str(dataset))
    submitted_yaml = f"""
agents:
  - name: codeagent
    kwargs:
      binary_path: {codeagent}
datasets:
  - path: {dataset}
    task_names:
      - ok
environment:
  type: docker
  mounts:
    - type: bind
      source: {codeagent}
      target: /usr/local/bin/codeagentcli
"""

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["stderr"], "harborYaml": submitted_yaml},
    )

    derived_run = store.get_run(result["runId"])
    manifest = derived_run["sync_manifest"]
    assert manifest["datasetPath"] == str(dataset.resolve())
    assert manifest["bindAssets"] == [
        {"source": str(codeagent.resolve()), "kind": "file", "targetName": "codeagentcli"}
    ]


def test_start_rerun_rejects_legacy_active_status_on_original_run(coordinator, store):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.update_run_rerun_fields(run_id=run["run_id"], rerun_status="running")

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])

    assert exc.value.code == 409
    assert "already in progress" in exc.value.message
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_creates_derived_run_and_leaves_original_unchanged(store, tmp_path):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": "exc-a",
                "status": "errored",
                "error_text": "boom",
                "artifact_index": {"trialDir": "/tmp/jobs/old/exc-a__old"},
            },
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    original_run_before = store.get_run(run["run_id"])
    original_template_before = store.get_task_template(run["template_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(run["run_id"])

    assert result["parentRunId"] == run["run_id"]
    assert result["runId"] != run["run_id"]
    original_run_after = store.get_run(run["run_id"])
    original_template_after = store.get_task_template(run["template_id"])
    assert original_run_after["rerun_status"] == original_run_before["rerun_status"]
    assert original_run_after["rerun_job_id"] == original_run_before["rerun_job_id"]
    assert original_template_after == original_template_before

    derived_run = store.get_run(result["runId"])
    assert derived_run["parent_run_id"] == run["run_id"]
    assert derived_run["rerun_status"] == "syncing"
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["run_id"] == derived_run["run_id"]

    derived_primary = [
        batch for batch in store.list_batches_for_run(derived_run["run_id"])
        if batch["batch_kind"] == "primary"
    ]
    assert len(derived_primary) == 1
    assert store.list_case_runs(derived_primary[0]["batch_id"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["run_id"] == derived_run["run_id"]
    assert rerun_batch["parent_batch_id"] == derived_primary[0]["batch_id"]
    assert rerun_batch["parent_batch_id"] != parent["batch_id"]


def test_start_rerun_marks_derived_run_failed_when_asset_sync_start_raises(store):
    class RaisingAssetSyncer:
        def start_rerun_sync_async(self, *, job_id, run_id):
            raise RuntimeError(f"sync start failed for {job_id}:{run_id}")

    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=RaisingAssetSyncer())

    with pytest.raises(RuntimeError, match="sync start failed"):
        coordinator.start_rerun(run["run_id"])

    assert store.list_active_derived_reruns(run["run_id"]) == []
    derived_runs = _derived_runs_for_parent(store, run["run_id"])
    assert len(derived_runs) == 1
    failed_run = store.get_run(derived_runs[0]["run_id"])
    assert failed_run["rerun_status"] == "failed"
    job = store.get_run_rerun_job(failed_run["rerun_job_id"])
    assert job["status"] == "failed"
    assert "sync start failed" in job["error_text"]

    retry = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(run["run_id"])
    assert retry["rerunStatus"] == "syncing"


def test_start_rerun_reserves_active_child_before_second_start_can_pass_check(store, monkeypatch):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)
    original_create_run = store.create_run
    entered_first_child_create = Event()
    second_child_create_before_first_reserved = Event()
    release_first_child_create = Event()
    slow_once_lock = Lock()
    slow_once = {"pending": True}

    def slow_first_derived_create_run(*, template_id, display_name=None, parent_run_id=None):
        should_wait = False
        if parent_run_id == run["run_id"]:
            with slow_once_lock:
                if slow_once["pending"]:
                    slow_once["pending"] = False
                    should_wait = True
                elif not release_first_child_create.is_set():
                    second_child_create_before_first_reserved.set()
        if should_wait:
            entered_first_child_create.set()
            assert release_first_child_create.wait(timeout=5)
        return original_create_run(
            template_id=template_id,
            display_name=display_name,
            parent_run_id=parent_run_id,
        )

    monkeypatch.setattr(store, "create_run", slow_first_derived_create_run)
    results = []
    errors = []

    def start() -> None:
        try:
            results.append(coordinator.start_rerun(run["run_id"]))
        except Exception as exc:
            errors.append(exc)

    first = Thread(target=start)
    first.start()
    assert entered_first_child_create.wait(timeout=5)
    second = Thread(target=start)
    second.start()
    second_child_create_before_first_reserved.wait(timeout=1)
    release_first_child_create.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not second_child_create_before_first_reserved.is_set()
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RerunValidationError)
    assert errors[0].code == 409
    assert [run["rerun_status"] for run in _derived_runs_for_parent(store, run["run_id"])] == [
        "syncing"
    ]


def test_start_rerun_splits_same_worker_reruns_by_parent_batch(coordinator, store):
    run, first_parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom-a"},
            {"case_id": "ok-a", "status": "succeeded", "score": 1.0},
        ],
    )
    second_parent = store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["exc-b", "ok-b"],
        preferred_worker_id="worker-a",
        batch_options={"source": "second"},
    )
    store.update_batch_progress(
        batch_id=second_parent["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        cases=[
            {"caseId": "exc-b", "status": "errored", "errorText": "boom-b"},
            {"caseId": "ok-b", "status": "succeeded", "score": 1.0},
        ],
    )

    result = coordinator.start_rerun(run["run_id"])

    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["run_id"] == result["runId"]
    assert result["workerShards"] == {"worker-a": 2}
    rerun_batch_ids = job["rerun_batches"]["worker-a"]
    assert isinstance(rerun_batch_ids, list)
    assert len(rerun_batch_ids) == 2

    derived_primary = store.list_primary_batches_for_run(result["runId"])
    cloned_parent_by_cases = {
        tuple(batch["selected_case_ids"]): batch["batch_id"]
        for batch in derived_primary
    }
    expected_parent_by_case = {
        "exc-a": cloned_parent_by_cases[tuple(first_parent["selected_case_ids"])],
        "exc-b": cloned_parent_by_cases[tuple(second_parent["selected_case_ids"])],
    }
    for batch_id in rerun_batch_ids:
        rerun_batch = store.get_batch(batch_id)
        assert rerun_batch["batch_kind"] == "exception_rerun"
        assert len(rerun_batch["selected_case_ids"]) == 1
        case_id = rerun_batch["selected_case_ids"][0]
        assert rerun_batch["parent_batch_id"] == expected_parent_by_case[case_id]


def test_start_rerun_rejects_no_exceptions(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "ok", "status": "succeeded", "score": 1.0}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"])
    assert exc.value.code == 400


def test_start_rerun_filters_by_selected_error_types(coordinator, store):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    result = coordinator.start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["TimeoutError"]},
    )
    assert result["exceptionCount"] == 1
    assert result["selectedErrorTypes"] == ["TimeoutError"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["selected_error_types"] == ["TimeoutError"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_start_rerun_rejects_empty_selected_error_types(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"], config={"selectedErrorTypes": []})
    assert exc.value.code == 400
    assert "at least one" in exc.value.message.lower()


def test_start_rerun_rejects_unknown_error_type(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom", "metrics": {"errorType": "TimeoutError"}}],
    )
    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(run["run_id"], config={"selectedErrorTypes": ["DoesNotExist"]})
    assert exc.value.code == 400
    assert "invalid error type" in exc.value.message.lower()


def test_start_rerun_omitted_types_reruns_all(coordinator, store):
    run, _ = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "a", "metrics": {"errorType": "TimeoutError"}},
            {"case_id": "exc-b", "status": "errored", "error_text": "b", "metrics": {"errorType": "OtherError"}},
        ],
    )
    result = coordinator.start_rerun(run["run_id"], config={})
    assert result["exceptionCount"] == 2
    assert set(result["selectedErrorTypes"]) == {"TimeoutError", "OtherError"}


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


def _write_jobs_trial(job_dir, trial_name, *, task_name, exception_text=None):
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"trial_name": trial_name, "task_name": task_name}),
        encoding="utf-8",
    )
    if exception_text is not None:
        (trial_dir / "exception.txt").write_text(exception_text, encoding="utf-8")
    return trial_dir


def test_start_rerun_selects_trials_from_exception_txt_not_db_rows(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "db says errored"},
            {"case_id": "exc-b", "status": "errored", "error_text": "db says errored"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    jobs_root = tmp_path / "harbor" / "jobs"
    job_dir = jobs_root / sanitize_name(str(run["display_name"]))
    _write_jobs_trial(
        job_dir,
        "exc-a__old",
        task_name="exc-a",
        exception_text="Traceback (most recent call last):\nTimeoutError: timed out\n",
    )
    _write_jobs_trial(job_dir, "exc-b__old", task_name="exc-b")
    _write_jobs_trial(job_dir, "ok__old", task_name="ok")
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(jobs_root)},
    )

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(run["run_id"])

    assert result["exceptionCount"] == 1
    assert result["selectedErrorTypes"] == ["TimeoutError"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["worker_shards"] == {"worker-a": ["exc-a"]}
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_start_rerun_filters_selected_types_from_exception_txt(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "succeeded", "score": 1.0},
            {"case_id": "exc-b", "status": "succeeded", "score": 1.0},
        ],
    )
    jobs_root = tmp_path / "harbor" / "jobs"
    job_dir = jobs_root / sanitize_name(str(run["display_name"]))
    _write_jobs_trial(
        job_dir,
        "exc-a__old",
        task_name="exc-a",
        exception_text="Traceback (most recent call last):\nPermissionError: denied\n",
    )
    _write_jobs_trial(
        job_dir,
        "exc-b__old",
        task_name="exc-b",
        exception_text="Traceback (most recent call last):\nAgentTimeoutError: timed out\n",
    )
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(jobs_root)},
    )

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["PermissionError"]},
    )

    assert result["exceptionCount"] == 1
    assert result["selectedErrorTypes"] == ["PermissionError"]
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == ["exc-a"]


def test_start_rerun_maps_harbor_trial_task_name_to_selected_case_id(store, tmp_path):
    dataset = tmp_path / "dataset"
    full_case_id = "instance_element-hq__element-web-4fec436883b601a3cac2d4a58067e597f737b817-vnan"
    (dataset / full_case_id).mkdir(parents=True)
    template = store.create_task_template(
        owner="default",
        name="exc-test",
        dataset_ref=str(dataset),
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="swe-p-0001")
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
        selected_case_ids=[full_case_id],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        cases=[
            {
                "caseId": "instance_element-hq__element-web",
                "status": "succeeded",
                "score": 1.0,
                "artifactIndex": {
                    "trialDir": str(tmp_path / "imported" / "instance_element-hq__element-web__abc")
                },
            }
        ],
    )
    jobs_root = tmp_path / "harbor" / "jobs"
    job_dir = jobs_root / "swe-p-0001"
    _write_jobs_trial(
        job_dir,
        "instance_element-hq__element-web__abc",
        task_name=full_case_id,
        exception_text="Traceback (most recent call last):\nPermissionError: denied\n",
    )
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(jobs_root)},
    )

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["PermissionError"]},
    )

    assert result["exceptionCount"] == 1
    assert result["workerShards"] == {"worker-a": 1}
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == [full_case_id]


def test_start_rerun_maps_prefixed_harbor_task_name_to_short_selected_case_id(store, tmp_path):
    dataset = tmp_path / "terminal-bench-2"
    short_case_id = "adaptive-rejection-sampler"
    (dataset / short_case_id).mkdir(parents=True)
    template = store.create_task_template(
        owner="default",
        name="exc-test",
        dataset_ref=str(dataset),
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="tb2-bitfun-ds-0004")
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
        selected_case_ids=[short_case_id],
        preferred_worker_id="worker-a",
        batch_options={},
    )
    store.update_batch_progress(
        batch_id=batch["batch_id"],
        worker_id="worker-a",
        status="succeeded",
        current_step=None,
        finished=True,
        cases=[
            {
                "caseId": short_case_id,
                "status": "succeeded",
                "score": 0.0,
            }
        ],
    )
    jobs_root = tmp_path / "harbor" / "jobs"
    job_dir = jobs_root / "tb2-bitfun-ds-0004"
    _write_jobs_trial(
        job_dir,
        f"{short_case_id}__npJJrTn",
        task_name=f"terminal-bench/{short_case_id}",
        exception_text="Traceback (most recent call last):\nAgentTimeoutError: timed out\n",
    )
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(jobs_root)},
    )

    result = RunRerunCoordinator(store=store, asset_syncer=None).start_rerun(
        run["run_id"],
        config={"selectedErrorTypes": ["AgentTimeoutError"]},
    )

    assert result["exceptionCount"] == 1
    assert result["workerShards"] == {"worker-a": 1}
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == [short_case_id]


def test_derived_rerun_job_name_uses_root_name_for_chained_reruns():
    assert (
        derived_rerun_job_name(
            source_job_name="swe-p-0001-rerun-run-11e2e2e22838",
            run_id="run-ebd84184539b",
        )
        == "swe-p-0001-rerun-run-ebd84184539b"
    )
    assert (
        derived_rerun_job_name(
            source_job_name="swe-p-0001-rerun-run-11e2e2e22838-rerun-run-ebd84184539b",
            run_id="run-next123",
        )
        == "swe-p-0001-rerun-run-next123"
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
    original_template = store.get_task_template(run["template_id"])
    original_run = store.get_run(run["run_id"])
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
    assert store.get_task_template(run["template_id"]) == original_template
    assert store.get_run(run["run_id"]) == original_run
    derived_run = store.get_run(result["runId"])
    template = store.get_task_template(derived_run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    executor_config = template["executor_config"]
    assert executor_config["nConcurrent"] == 3
    assert executor_config["timeoutMultiplier"] == 1.4
    assert executor_config["agentTimeoutMultiplier"] == 3.4
    assert executor_config["verifierTimeoutMultiplier"] == 2.4
    assert executor_config["environmentBuildTimeoutMultiplier"] == 1.8
    expected_jobs_dir = derived_jobs_dir_for_run(store=store, run=derived_run)
    assert executor_config["combinedJobsDir"] == str(expected_jobs_dir)
    assert executor_config["combinedJobsDir"] != assets["jobsDir"]
    manifest = derived_run["sync_manifest"]
    assert manifest["datasetPath"] == assets["datasetPath"]
    assert manifest["bitfunCliPath"] == assets["bitfunCliPath"]
    assert manifest["bitfunConfigDir"] == assets["bitfunConfigDir"]
    assert manifest["workers"]["worker-a"]["targetRoot"] == str(tmp_path / "shared" / "sync" / result["runId"])
    assert manifest["workers"]["worker-a"]["targetRoot"] != previous_target
    assert manifest["workers"]["worker-a"]["transport"] == "local"
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["run_id"] == result["runId"]
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_options"]["concurrency"] == 3


def test_start_rerun_clones_only_source_job_to_final_rerun_job_without_pruning(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": "exc-a",
                "status": "errored",
                "error_text": "boom",
                "metrics": {"errorType": "TimeoutError"},
            },
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    original_jobs_dir = tmp_path / "original-harbor" / "jobs"
    original_job = original_jobs_dir / sanitize_name(str(run["display_name"]))
    errored_trial = _write_jobs_trial(original_job, "exc-a__old", task_name="exc-a")
    (errored_trial / "exception.txt").write_text(
        "Traceback (most recent call last):\nTimeoutError: boom\n",
        encoding="utf-8",
    )
    succeeded_trial = _write_jobs_trial(original_job, "ok__old", task_name="ok")
    (original_job / "config.json").write_text(
        json.dumps({"job_name": original_job.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    unrelated_job = original_jobs_dir / "unrelated-job"
    _write_jobs_trial(unrelated_job, "other__old", task_name="other")
    (unrelated_job / "config.json").write_text(
        json.dumps({"job_name": unrelated_job.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    submitted_jobs_dir = tmp_path / "submitted-override" / "jobs"
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(original_jobs_dir), "nConcurrent": 1},
    )
    original_template = store.get_task_template(run["template_id"])
    original_run = store.get_run(run["run_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": str(submitted_jobs_dir),
            "executorConfig": {"nConcurrent": 2},
            "selectedErrorTypes": ["TimeoutError"],
        },
    )

    assert store.get_task_template(run["template_id"]) == original_template
    assert store.get_run(run["run_id"]) == original_run
    assert errored_trial.exists()
    assert succeeded_trial.exists()

    derived_run = store.get_run(result["runId"])
    derived_template = store.get_task_template(derived_run["template_id"])
    executor_config = derived_template["executor_config"]
    final_job_dir = original_jobs_dir / f"{original_job.name}-rerun-{result['runId']}"
    assert derived_template["dataset_ref"] == assets["datasetPath"]
    assert executor_config["nConcurrent"] == 2
    assert executor_config["combinedJobsDir"] == str(original_jobs_dir)
    assert executor_config["combinedJobsDir"] != str(submitted_jobs_dir)
    assert final_job_dir.exists()
    assert (final_job_dir / "exc-a__old" / "result.json").exists()
    assert (final_job_dir / "exc-a__old" / "exception.txt").exists()
    assert (final_job_dir / "ok__old" / "result.json").exists()
    assert not (original_jobs_dir / f"unrelated-job-rerun-{result['runId']}").exists()
    assert not (final_job_dir / "unrelated-job").exists()


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
    assert _derived_runs_for_parent(store, run["run_id"]) == []
    assert store.get_task_template(run["template_id"])["dataset_ref"] == original_template["dataset_ref"]


def test_start_rerun_rejects_missing_local_worker_shared_root_before_deriving_run(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    store.register_worker(
        worker_id="worker-a",
        display_name="worker-a",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"localToController": True},
    )
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
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
    assert "missing capabilities.sharedRoot" in exc.value.message
    assert store.list_active_derived_reruns(run["run_id"]) == []
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_resolves_truncated_case_ids_to_dataset_dirs(store, tmp_path):
    long_selected = (
        "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0af01760a2d4dc-"
        "vc4e41fd0029957297843cb9dec4a25c7c756f029"
    )
    short_case_id = "instance_tutao__tutanota-fb32e5f"
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": short_case_id,
                "status": "errored",
                "error_text": "boom",
                "artifact_index": {
                    "trialDir": f"/tmp/jobs/batch/{short_case_id}__XsXcKQq",
                },
            }
        ],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE batches SET selected_case_ids_json = ? WHERE batch_id = ?",
            (json.dumps([long_selected], ensure_ascii=False), parent["batch_id"]),
        )
        conn.commit()
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, [long_selected])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        },
    )

    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == [long_selected]
    assert job["worker_shards"]["worker-a"] == [long_selected]


def test_start_rerun_preserves_copied_short_id_trial_when_case_id_resolves(store, tmp_path):
    long_selected = (
        "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0af01760a2d4dc-"
        "vc4e41fd0029957297843cb9dec4a25c7c756f029"
    )
    short_case_id = "instance_tutao__tutanota-fb32e5f"
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {
                "case_id": short_case_id,
                "status": "errored",
                "error_text": "boom",
                "artifact_index": {
                    "trialDir": f"/tmp/jobs/batch/{short_case_id}__XsXcKQq",
                },
            },
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE batches SET selected_case_ids_json = ? WHERE batch_id = ?",
            (json.dumps([long_selected, "ok"], ensure_ascii=False), parent["batch_id"]),
        )
        conn.commit()
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, [long_selected])
    original_jobs_dir = tmp_path / "original-harbor" / "jobs"
    original_job = original_jobs_dir / sanitize_name(str(run["display_name"]))
    short_trial = _write_jobs_trial(
        original_job,
        f"{short_case_id}__old",
        task_name=short_case_id,
    )
    (short_trial / "exception.txt").write_text(
        "Traceback (most recent call last):\nRuntimeError: boom\n",
        encoding="utf-8",
    )
    ok_trial = _write_jobs_trial(original_job, "ok__old", task_name="ok")
    (original_job / "config.json").write_text(
        json.dumps({"job_name": original_job.name, "jobs_dir": str(original_jobs_dir)}),
        encoding="utf-8",
    )
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(original_jobs_dir)},
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        },
    )

    assert short_trial.exists()
    assert ok_trial.exists()
    derived_run = store.get_run(result["runId"])
    final_job_dir = original_jobs_dir / f"{original_job.name}-rerun-{result['runId']}"
    assert (final_job_dir / f"{short_case_id}__old" / "result.json").exists()
    assert (final_job_dir / "ok__old" / "result.json").exists()
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["selected_case_ids"] == [long_selected]


def test_start_rerun_persists_job_error_when_source_jobs_copy_fails(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    missing_jobs_dir = tmp_path / "missing-harbor" / "jobs"
    store.update_task_template_executor_config(
        run["template_id"],
        {"combinedJobsDir": str(missing_jobs_dir)},
    )
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    with pytest.raises(RuntimeError) as exc:
        coordinator.start_rerun(run["run_id"])

    assert "source job directory not found" in str(exc.value)
    derived_runs = _derived_runs_for_parent(store, run["run_id"])
    assert len(derived_runs) == 1
    failed_run = store.get_run(derived_runs[0]["run_id"])
    assert failed_run["rerun_status"] == "failed"
    assert failed_run["rerun_job_id"]
    job = store.get_run_rerun_job(failed_run["rerun_job_id"])
    assert job is not None
    assert job["run_id"] == failed_run["run_id"]
    assert job["status"] == "failed"
    assert job["rerun_batches"] == {}
    assert "source job directory not found" in job["error_text"]


def test_start_rerun_rejects_malformed_executor_config_before_job_creation(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": "bad",
            },
        )

    assert exc.value.code == 400
    assert exc.value.message == "executorConfig must be an object"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert _derived_runs_for_parent(store, run["run_id"]) == []

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": [],
            },
        )

    assert exc.value.code == 400
    assert exc.value.message == "executorConfig must be an object"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_rejects_invalid_executor_numbers_before_job_creation(store, tmp_path):
    run, _parent = seed_finished_run_with_cases(
        store,
        cases=[{"case_id": "exc-a", "status": "errored", "error_text": "boom"}],
    )
    _make_worker_local(store, tmp_path)
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": {
                    "nConcurrent": -1,
                    "timeoutMultiplier": 1.0,
                },
            },
        )

    assert exc.value.code == 400
    assert exc.value.message == "executorConfig.nConcurrent must be a positive integer"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert _derived_runs_for_parent(store, run["run_id"]) == []

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": {
                    "nConcurrent": 1,
                    "timeoutMultiplier": 0,
                },
            },
        )

    assert exc.value.code == 400
    assert exc.value.message == "executorConfig.timeoutMultiplier must be a positive number"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert _derived_runs_for_parent(store, run["run_id"]) == []

    with pytest.raises(RerunValidationError) as exc:
        coordinator.start_rerun(
            run["run_id"],
            config={
                "datasetPath": assets["datasetPath"],
                "bitfunCliPath": assets["bitfunCliPath"],
                "bitfunConfigDir": assets["bitfunConfigDir"],
                "jobsDir": assets["jobsDir"],
                "executorConfig": {
                    "nConcurrent": 1.5,
                    "timeoutMultiplier": 1.0,
                },
            },
        )

    assert exc.value.code == 400
    assert exc.value.message == "executorConfig.nConcurrent must be a positive integer"
    assert store.get_active_run_rerun_job(run["run_id"]) is None
    assert _derived_runs_for_parent(store, run["run_id"]) == []


def test_start_rerun_empty_executor_config_preserves_existing_behavior(store):
    run, parent = seed_finished_run_with_cases(
        store,
        cases=[
            {"case_id": "exc-a", "status": "errored", "error_text": "boom"},
            {"case_id": "ok", "status": "succeeded", "score": 1.0},
        ],
    )
    original_template = store.get_task_template(run["template_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(run["run_id"], config={"executorConfig": {}})

    template = store.get_task_template(run["template_id"])
    assert template["dataset_ref"] == original_template["dataset_ref"]
    assert template["executor_config"] == original_template["executor_config"]
    derived_run = store.get_run(result["runId"])
    derived_template = store.get_task_template(derived_run["template_id"])
    assert derived_template["executor_config"]["combinedJobsDir"] == str(
        derived_jobs_dir_for_run(store=store, run=derived_run)
    )
    job = store.get_run_rerun_job(result["rerunJobId"])
    rerun_batch = store.get_batch(job["rerun_batches"]["worker-a"])
    assert rerun_batch["batch_options"] == parent["batch_options"]


def test_start_rerun_dataset_change_with_empty_executor_config_updates_template_and_manifest(
    store,
    tmp_path,
    monkeypatch,
):
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
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "workers": {
                "worker-a": {
                    "caseIds": ["exc-a", "ok"],
                    "targetRoot": previous_target,
                    "transport": "local",
                }
            },
        },
    )
    original_template = store.get_task_template(run["template_id"])
    original_run = store.get_run(run["run_id"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)
    monkeypatch.setattr(
        rerun_coordinator_module,
        "RERUN_CONFIG_KEYS",
        ["executorConfig", "datasetPath", "bitfunCliPath", "bitfunConfigDir", "jobsDir"],
    )

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "executorConfig": {},
        },
    )

    assert store.get_task_template(run["template_id"]) == original_template
    assert store.get_run(run["run_id"]) == original_run
    derived_run = store.get_run(result["runId"])
    template = store.get_task_template(derived_run["template_id"])
    assert template["dataset_ref"] == assets["datasetPath"]
    manifest = derived_run["sync_manifest"]
    assert manifest["datasetPath"] == assets["datasetPath"]
    assert manifest["bitfunCliPath"] == assets["bitfunCliPath"]
    assert manifest["bitfunConfigDir"] == assets["bitfunConfigDir"]
    assert manifest["workers"]["worker-a"]["targetRoot"] == str(tmp_path / "shared" / "sync" / result["runId"])
    assert manifest["workers"]["worker-a"]["targetRoot"] != previous_target
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["run_id"] == result["runId"]


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
            "modelNameByWorker": {
                "worker-a": "model-a",
                "worker-b": "model-b",
            },
            "agentKwargsByWorker": {
                "worker-a": {"version": "a"},
                "worker-b": {"version": "b"},
            },
            "agentEnvByWorker": {
                "worker-a": {"A": "1"},
                "worker-b": {"B": "2"},
            },
            "processEnvByWorker": {
                "worker-a": {"PA": "1"},
                "worker-b": {"PB": "2"},
            },
            "customByWorker": {
                "worker-a": {"custom": "a"},
                "worker-b": {"custom": "b"},
            },
        },
    )
    original_template = store.get_task_template(run["template_id"])
    assets = _prepare_rerun_assets(tmp_path, ["exc-a"])
    coordinator = RunRerunCoordinator(store=store, asset_syncer=None)

    result = coordinator.start_rerun(
        run["run_id"],
        config={
            "datasetPath": assets["datasetPath"],
            "bitfunCliPath": assets["bitfunCliPath"],
            "bitfunConfigDir": assets["bitfunConfigDir"],
            "jobsDir": assets["jobsDir"],
            "executorConfig": {"nConcurrent": 2},
        },
    )

    assert store.get_task_template(run["template_id"]) == original_template
    derived_run = store.get_run(result["runId"])
    job = store.get_run_rerun_job(result["rerunJobId"])
    assert job["run_id"] == result["runId"]
    executor_config = store.get_task_template(derived_run["template_id"])["executor_config"]
    for key in (
        "uvBinaryByWorker",
        "harborRepoPathByWorker",
        "datasetPathByWorker",
        "mountsByWorker",
        "modelNameByWorker",
        "agentKwargsByWorker",
        "agentEnvByWorker",
        "processEnvByWorker",
        "customByWorker",
    ):
        assert "worker-b" not in executor_config[key]
