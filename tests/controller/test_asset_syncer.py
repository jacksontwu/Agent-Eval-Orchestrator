import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_eval_orchestrator.controller.asset_syncer import (
    AssetSyncer,
    build_sync_manifest,
    initial_worker_steps,
    is_local_worker,
    set_worker_step_status,
    sync_bitfun_local,
    sync_cases_local,
    validate_create_task_assets,
    worker_executor_paths,
)
from agent_eval_orchestrator.core.ids import new_id


def test_is_local_worker_by_flag():
    worker = {"capabilities": {"localToController": True}}
    assert is_local_worker(worker, Path("/tmp/controller")) is True


def test_is_local_worker_by_existing_shared_root(tmp_path):
    shared = tmp_path / "runtime"
    shared.mkdir(exist_ok=True)
    worker = {"capabilities": {"sharedRoot": str(shared)}}
    assert is_local_worker(worker, tmp_path) is True


def test_validate_create_task_assets(tmp_path, store):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()

    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(tmp_path / "runtime")},
    )
    validate_create_task_assets(
        dataset_path=dataset,
        bitfun_cli_path=bitfun_cli,
        bitfun_config_dir=config_dir,
        case_ids=["case-a"],
        workers=store.list_workers(),
        worker_ids=["local-a"],
        controller_shared_root=tmp_path,
    )


def test_validate_rejects_remote_without_ssh(tmp_path, store):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()

    store.register_worker(
        worker_id="remote-a",
        display_name="remote",
        host="remote",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": "/nonexistent/on/controller/runtime"},
    )
    with pytest.raises(RuntimeError, match="ssh_host_alias"):
        validate_create_task_assets(
            dataset_path=dataset,
            bitfun_cli_path=bitfun_cli,
            bitfun_config_dir=config_dir,
            case_ids=["case-a"],
            workers=store.list_workers(),
            worker_ids=["remote-a"],
            controller_shared_root=tmp_path,
        )


def test_build_sync_manifest(tmp_path):
    (tmp_path / "runtime").mkdir()
    manifest = build_sync_manifest(
        run_id="run-abc",
        dataset_path=Path("/ctrl/dataset"),
        bitfun_cli_path=Path("/ctrl/bitfun-cli"),
        bitfun_config_dir=Path("/ctrl/.config/bitfun"),
        worker_shards={"remote-a": ["case-1"], "local-a": ["case-2"]},
        workers_by_id={
            "remote-a": {"worker_id": "remote-a", "ssh_host_alias": "aeo-ecs-0004", "capabilities": {"sharedRoot": "/home/djn/worker/runtime"}},
            "local-a": {"worker_id": "local-a", "capabilities": {"sharedRoot": str(tmp_path / "runtime")}},
        },
        controller_shared_root=tmp_path,
    )
    assert manifest["workers"]["remote-a"]["transport"] == "ssh"
    assert manifest["workers"]["remote-a"]["caseIds"] == ["case-1"]
    assert manifest["workers"]["local-a"]["transport"] == "local"
    assert manifest["workers"]["remote-a"]["targetRoot"].endswith("/sync/run-abc")


def test_worker_executor_paths():
    paths = worker_executor_paths("/tmp/sync/run-1")
    assert paths["datasetPath"] == "/tmp/sync/run-1/dataset"
    assert paths["mounts"][0]["target"] == "/usr/local/bin/bitfun-cli"
    assert paths["agentEnv"]["XDG_CONFIG_HOME"] == "/testbed/.config"


def test_initial_worker_steps_and_status():
    steps = initial_worker_steps(["worker-a", "worker-b"])
    assert len(steps) == 2
    updated = set_worker_step_status(steps, "worker-a", "sync_cases", "running")
    worker_a = next(item for item in updated if item["workerId"] == "worker-a")
    assert worker_a["steps"][0]["status"] == "running"


def test_sync_cases_local(tmp_path):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("x", encoding="utf-8")
    target = tmp_path / "target"
    sync_cases_local(dataset_path=dataset, case_ids=["case-a"], target_dataset_dir=target / "dataset")
    assert (target / "dataset" / "case-a" / "task.toml").read_text(encoding="utf-8") == "x"


def test_sync_bitfun_local_preserves_executable(tmp_path):
    cli = tmp_path / "bitfun-cli"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.toml").write_text("a=1", encoding="utf-8")
    target = tmp_path / "target"
    sync_bitfun_local(
        bitfun_cli_path=cli,
        bitfun_config_dir=config,
        target_bitfun_dir=target / "bitfun",
    )
    copied = target / "bitfun" / "bitfun-cli"
    assert copied.exists()
    assert os.access(copied, os.X_OK)
    assert (target / "bitfun" / "config" / "settings.toml").exists()


def test_sync_cases_remote_uses_rsync(sample_ssh_config, tmp_path):
    from agent_eval_orchestrator.controller.asset_syncer import sync_cases_remote

    dataset = tmp_path / "dataset"
    (dataset / "case-a").mkdir(parents=True)
    runner = MagicMock()
    sync_cases_remote(
        ssh=runner,
        host_alias="aeo-ecs-0004",
        dataset_path=dataset,
        case_ids=["case-a"],
        target_root="/tmp/sync/run-1",
    )
    runner.remote_mkdir_p.assert_called()
    assert runner.rsync_dir.call_count == 1


def test_asset_syncer_promotes_batches_on_success(store, tmp_path, sample_ssh_config):
    dataset = tmp_path / "dataset"
    case_a = dataset / "case-a"
    case_a.mkdir(parents=True)
    (case_a / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    config_dir.mkdir()
    shared = tmp_path / "runtime"

    store.register_worker(
        worker_id="local-a",
        display_name="local",
        host="localhost",
        slots_total=1,
        slots_used=0,
        capabilities={"sharedRoot": str(shared), "localToController": True},
    )
    template = store.create_task_template(
        owner="default",
        name="sync-run",
        dataset_ref=str(dataset),
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"])
    store.create_batch(
        run_id=run["run_id"],
        selected_case_ids=["case-a"],
        preferred_worker_id="local-a",
        batch_options={},
        initial_status="pending_sync",
    )
    manifest = {
        "datasetPath": str(dataset),
        "bitfunCliPath": str(bitfun_cli),
        "bitfunConfigDir": str(config_dir),
        "workers": {
            "local-a": {
                "caseIds": ["case-a"],
                "targetRoot": str(shared / "sync" / run["run_id"]),
                "transport": "local",
            }
        },
    }
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="pending",
        sync_manifest=manifest,
    )
    job_id = new_id("sync")
    store.create_asset_sync_job(job_id=job_id, run_id=run["run_id"], steps=[])

    syncer = AssetSyncer(store=store, ssh_config_path=sample_ssh_config, controller_shared_root=tmp_path)
    syncer.run_job(job_id=job_id, run_id=run["run_id"], template_id=template["template_id"])

    updated_run = store.get_run(run["run_id"])
    assert updated_run["sync_status"] == "succeeded"
    job = store.get_asset_sync_job(job_id)
    assert job["status"] == "succeeded"
    claimed = store.claim_next_batch("local-a")
    assert claimed is not None
    updated_template = store.get_task_template(template["template_id"])
    assert updated_template["executor_config"]["datasetPathByWorker"]["local-a"].endswith("/dataset")


def test_cleanup_run_sync_assets_local(store, tmp_path, sample_ssh_config):
    shared = tmp_path / "runtime"
    target = shared / "sync" / "run-clean"
    (target / "dataset" / "case-a").mkdir(parents=True)
    template = store.create_task_template(
        owner="default",
        name="cleanup",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={},
        model_profile_ref=None,
        note="",
    )
    run = store.create_run(template_id=template["template_id"], display_name="cleanup")
    store.update_run_sync_fields(
        run_id=run["run_id"],
        sync_status="succeeded",
        sync_manifest={
            "workers": {
                "local-a": {
                    "targetRoot": str(target),
                    "transport": "local",
                    "caseIds": ["case-a"],
                }
            }
        },
    )
    syncer = AssetSyncer(store=store, ssh_config_path=sample_ssh_config, controller_shared_root=tmp_path)
    syncer.cleanup_run_sync_assets(run["run_id"])
    assert not target.exists()
    updated = store.get_run(run["run_id"])
    assert updated["sync_status"] == "cleaned"
