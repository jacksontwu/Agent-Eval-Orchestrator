from __future__ import annotations

import ipaddress
import os
import shutil
import socket
from pathlib import Path
from typing import Any

from app.core.worker_paths import build_sync_bind_mounts

SYNC_STEP_LABELS = {
    "sync_cases": "同步 dataset case",
    "sync_bitfun": "同步 bitfun-cli",
}


def is_local_worker(worker: dict[str, Any], controller_shared_root: Path) -> bool:
    caps = worker.get("capabilities") or {}
    if caps.get("localToController") is True:
        return True
    shared_root = str(caps.get("sharedRoot") or "").strip()
    if not shared_root:
        return False
    shared_path = Path(shared_root).expanduser()
    if not shared_path.exists():
        return False
    try:
        shared_path.resolve().relative_to(controller_shared_root.expanduser().resolve())
        return True
    except ValueError:
        pass
    host = str(worker.get("host") or "").strip()
    if not host:
        return False
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        local_names = {name for name in (socket.gethostname(), socket.getfqdn(), "localhost") if name}
        return host in local_names
    return False


def initial_worker_steps(worker_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "workerId": worker_id,
            "steps": [
                {"id": "sync_cases", "label": SYNC_STEP_LABELS["sync_cases"], "status": "pending"},
                {"id": "sync_bitfun", "label": SYNC_STEP_LABELS["sync_bitfun"], "status": "pending"},
            ],
        }
        for worker_id in worker_ids
    ]


def rerun_needs_asset_sync(manifest: dict[str, Any]) -> bool:
    return bool(str(manifest.get("datasetPath") or "").strip())


def set_worker_step_status(
    steps: list[dict[str, Any]],
    worker_id: str,
    step_id: str,
    status: str,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for worker_entry in steps:
        entry = dict(worker_entry)
        if entry["workerId"] != worker_id:
            updated.append(entry)
            continue
        next_steps = []
        for step in entry["steps"]:
            item = dict(step)
            if item["id"] == step_id:
                item["status"] = status
            next_steps.append(item)
        entry["steps"] = next_steps
        updated.append(entry)
    return updated


def validate_create_task_assets(
    *,
    dataset_path: Path,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    case_ids: list[str],
    workers: list[dict[str, Any]],
    worker_ids: list[str],
    controller_shared_root: Path,
) -> None:
    if not dataset_path.exists() or not dataset_path.is_dir():
        raise RuntimeError(f"datasetPath not found: {dataset_path}")
    if not bitfun_cli_path.exists() or not os.access(bitfun_cli_path, os.X_OK):
        raise RuntimeError(f"bitfunCliPath must exist and be executable: {bitfun_cli_path}")
    if not bitfun_config_dir.exists() or not bitfun_config_dir.is_dir():
        raise RuntimeError(f"bitfunConfigDir must be an existing directory: {bitfun_config_dir}")
    if not (bitfun_config_dir / "config").is_dir():
        raise RuntimeError(f"bitfunConfigDir must contain a config directory: {bitfun_config_dir / 'config'}")
    if not worker_ids:
        raise RuntimeError("workerIds must not be empty")
    workers_by_id = {str(item["worker_id"]): item for item in workers}
    for case_id in case_ids:
        case_dir = dataset_path / case_id
        if not case_dir.is_dir():
            raise RuntimeError(f"case directory not found: {case_id}")
    for worker_id in worker_ids:
        worker = workers_by_id.get(worker_id)
        if not worker:
            raise RuntimeError(f"worker not found: {worker_id}")
        # No SSH requirement anymore: remote workers pull assets over HTTP.


def _worker_target_root(worker: dict[str, Any], run_id: str) -> str:
    shared_root = str((worker.get("capabilities") or {}).get("sharedRoot") or "").strip()
    if not shared_root:
        raise RuntimeError(f"worker {worker['worker_id']} missing capabilities.sharedRoot")
    return str(Path(shared_root).expanduser() / "sync" / run_id)


def build_sync_manifest(
    *,
    run_id: str,
    dataset_path: Path,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    worker_shards: dict[str, list[str]],
    workers_by_id: dict[str, dict[str, Any]],
    controller_shared_root: Path,
) -> dict[str, Any]:
    workers: dict[str, Any] = {}
    for worker_id, case_ids in worker_shards.items():
        worker = workers_by_id[worker_id]
        local = is_local_worker(worker, controller_shared_root)
        entry: dict[str, Any] = {
            "caseIds": case_ids,
            "targetRoot": _worker_target_root(worker, run_id),
            "transport": "local" if local else "http",
        }
        workers[worker_id] = entry
    return {
        "datasetPath": str(dataset_path),
        "bitfunCliPath": str(bitfun_cli_path),
        "bitfunConfigDir": str(bitfun_config_dir),
        "workers": workers,
    }


def worker_executor_paths(*, target_root: str, uv_binary: str) -> dict[str, Any]:
    root = str(Path(target_root))
    return {
        "datasetPath": f"{root}/dataset",
        "mounts": build_sync_bind_mounts(uv_binary=uv_binary, sync_root=root),
    }


def sync_cases_local(*, dataset_path: Path, case_ids: list[str], target_dataset_dir: Path) -> None:
    target_dataset_dir.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        src = dataset_path / case_id
        dst = target_dataset_dir / case_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def sync_bitfun_local(
    *,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    target_bitfun_dir: Path,
) -> None:
    target_bitfun_dir.mkdir(parents=True, exist_ok=True)
    target_cli = target_bitfun_dir / "bitfun-cli"
    shutil.copy2(bitfun_cli_path, target_cli)
    os.chmod(target_cli, os.stat(bitfun_cli_path).st_mode)
    target_config = target_bitfun_dir / "config"
    if target_config.exists():
        shutil.rmtree(target_config)
    shutil.copytree(bitfun_config_dir / "config", target_config)


def cleanup_sync_target_local(target_root: Path) -> None:
    shutil.rmtree(target_root, ignore_errors=True)
