import os
from pathlib import Path

from app.service.orchestration.asset_syncer import (
    build_sync_manifest,
    initial_worker_steps,
    is_local_worker,
    set_worker_step_status,
    sync_bitfun_local,
    sync_cases_local,
    validate_create_task_assets,
    worker_executor_paths,
)


def test_is_local_worker_by_flag():
    assert is_local_worker({"capabilities": {"localToController": True}}, Path("/tmp/controller")) is True


def test_is_local_worker_by_existing_shared_root(tmp_path):
    shared = tmp_path / "runtime"
    shared.mkdir()
    assert is_local_worker({"capabilities": {"sharedRoot": str(shared)}}, tmp_path) is True


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
    config_root = tmp_path / "bitfun-root"
    config = config_root / "config"
    config.mkdir(parents=True)
    (config / "app.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "target"
    sync_bitfun_local(bitfun_cli_path=cli, bitfun_config_dir=config_root, target_bitfun_dir=target / "bitfun")
    copied = target / "bitfun" / "bitfun-cli"
    assert copied.exists() and os.access(copied, os.X_OK)
    assert (target / "bitfun" / "config" / "app.json").exists()


def test_build_sync_manifest_uses_http_for_remote(tmp_path):
    (tmp_path / "runtime").mkdir()
    manifest = build_sync_manifest(
        run_id="run-abc",
        dataset_path=Path("/ctrl/dataset"),
        bitfun_cli_path=Path("/ctrl/bitfun-cli"),
        bitfun_config_dir=Path("/ctrl/.config/bitfun"),
        worker_shards={"remote-a": ["case-1"], "local-a": ["case-2"]},
        workers_by_id={
            "remote-a": {"worker_id": "remote-a", "capabilities": {"sharedRoot": "/home/djn/worker/runtime"}},
            "local-a": {"worker_id": "local-a", "capabilities": {"sharedRoot": str(tmp_path / "runtime")}},
        },
        controller_shared_root=tmp_path,
    )
    assert manifest["workers"]["remote-a"]["transport"] == "http"
    assert "sshHostAlias" not in manifest["workers"]["remote-a"]
    assert manifest["workers"]["local-a"]["transport"] == "local"
    assert manifest["workers"]["remote-a"]["targetRoot"].endswith("/sync/run-abc")


def test_validate_allows_remote_without_ssh(tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "case-a").mkdir(parents=True)
    (dataset / "case-a" / "task.toml").write_text("", encoding="utf-8")
    bitfun_cli = tmp_path / "bitfun-cli"
    bitfun_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(bitfun_cli, 0o755)
    config_dir = tmp_path / "bitfun-config"
    (config_dir / "config").mkdir(parents=True)
    workers = [{"worker_id": "remote-a", "host": "203.0.113.10",
                "capabilities": {"sharedRoot": "/nonexistent/runtime"}}]
    # must not raise even though worker is remote and has no ssh_host_alias
    validate_create_task_assets(
        dataset_path=dataset, bitfun_cli_path=bitfun_cli, bitfun_config_dir=config_dir,
        case_ids=["case-a"], workers=workers, worker_ids=["remote-a"],
        controller_shared_root=tmp_path / "controller-runtime",
    )


def test_worker_executor_paths():
    paths = worker_executor_paths(target_root="/tmp/sync/run-1", uv_binary="/home/djn/.local/bin/uv")
    assert paths["datasetPath"] == "/tmp/sync/run-1/dataset"
    assert paths["mounts"][1]["source"] == "/tmp/sync/run-1/bitfun/bitfun-cli"


def test_initial_worker_steps_and_status():
    steps = initial_worker_steps(["worker-a", "worker-b"])
    updated = set_worker_step_status(steps, "worker-a", "sync_cases", "running")
    worker_a = next(item for item in updated if item["workerId"] == "worker-a")
    assert worker_a["steps"][0]["status"] == "running"
