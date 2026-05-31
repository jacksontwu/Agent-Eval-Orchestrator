from __future__ import annotations

import os
import ipaddress
import shutil
import socket
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from agent_eval_orchestrator.controller.ssh_runner import SshRunner
from agent_eval_orchestrator.core.ids import new_id
from agent_eval_orchestrator.core.worker_paths import build_sync_bind_mounts

if TYPE_CHECKING:
    from agent_eval_orchestrator.storage.store import Store

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
        if is_local_worker(worker, controller_shared_root):
            continue
        if not str(worker.get("ssh_host_alias") or "").strip():
            raise RuntimeError(f"worker {worker_id} requires ssh_host_alias for remote asset sync")


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
            "transport": "local" if local else "ssh",
        }
        if not local:
            entry["sshHostAlias"] = str(worker["ssh_host_alias"])
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


def sync_cases_remote(
    *,
    ssh: SshRunner,
    host_alias: str,
    dataset_path: Path,
    case_ids: list[str],
    target_root: str,
) -> None:
    ssh.remote_mkdir_p(host_alias, f"{target_root}/dataset")
    for case_id in case_ids:
        ssh.rsync_dir(
            dataset_path / case_id,
            f"{host_alias}:{target_root}/dataset/{case_id}/",
            remote=True,
        )


def sync_bitfun_remote(
    *,
    ssh: SshRunner,
    host_alias: str,
    bitfun_cli_path: Path,
    bitfun_config_dir: Path,
    target_root: str,
) -> None:
    ssh.remote_mkdir_p(host_alias, f"{target_root}/bitfun")
    ssh.scp_file(bitfun_cli_path, f"{host_alias}:{target_root}/bitfun/bitfun-cli")
    ssh.rsync_dir(
        bitfun_config_dir / "config",
        f"{host_alias}:{target_root}/bitfun/config/",
        remote=True,
    )


def cleanup_sync_target_local(target_root: Path) -> None:
    shutil.rmtree(target_root, ignore_errors=True)


def cleanup_sync_target_remote(*, ssh: SshRunner, host_alias: str, target_root: str) -> None:
    ssh.remote_rm_rf(host_alias, target_root)


class AssetSyncer:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        controller_shared_root: Path,
    ) -> None:
        self.store = store
        self.controller_shared_root = controller_shared_root.expanduser().resolve()
        self._current_job_id = ""
        self.ssh = SshRunner(ssh_config_path, log_fn=self._log)

    def start_job_async(self, **kwargs: Any) -> None:
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        thread.start()

    def _log(self, chunk: str) -> None:
        if self._current_job_id:
            self.store.append_asset_sync_log(self._current_job_id, chunk)

    def run_job(self, *, job_id: str, run_id: str, template_id: str) -> None:
        self._current_job_id = job_id
        run = self.store.get_run(run_id)
        if not run:
            raise RuntimeError("run not found")
        manifest = dict(run.get("sync_manifest") or {})
        worker_entries = manifest.get("workers") or {}
        worker_ids = list(worker_entries.keys())
        steps = initial_worker_steps(worker_ids)
        self.store.update_asset_sync_job(job_id, status="running", steps=steps)
        self.store.update_run_sync_fields(run_id=run_id, sync_status="running")

        errors: list[str] = []
        lock = threading.Lock()
        template = self.store.get_task_template(template_id)
        executor_config = dict(template.get("executor_config") or {}) if template else {}

        def worker_thread(worker_id: str) -> None:
            nonlocal steps
            entry = worker_entries[worker_id]
            uv_binary = str((executor_config.get("uvBinaryByWorker") or {}).get(worker_id) or "")
            try:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "running")
                    self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_cases", steps=steps)
                self._sync_cases(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "running")
                    self.store.update_asset_sync_job(job_id, current_step=f"{worker_id}:sync_bitfun", steps=steps)
                self._sync_bitfun(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                    self.store.update_asset_sync_job(job_id, steps=steps)
                paths = worker_executor_paths(
                    target_root=str(entry["targetRoot"]),
                    uv_binary=uv_binary,
                )
                self.store.update_task_template_executor_config(
                    template_id,
                    {
                        "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                        "mountsByWorker": {worker_id: paths["mounts"]},
                    },
                )
                self.store.promote_worker_batches_to_queued(run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "failed")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "failed")
                    self.store.update_asset_sync_job(job_id, steps=steps)
                self.store.mark_worker_batches_sync_failed(run_id=run_id, worker_id=worker_id)
                errors.append(f"{worker_id}: {exc}")

        threads = [
            threading.Thread(target=worker_thread, args=(worker_id,), daemon=True)
            for worker_id in worker_ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            self.store.update_run_sync_fields(run_id=run_id, sync_status="failed")
            self.store.update_asset_sync_job(
                job_id,
                status="failed",
                steps=steps,
                error_text="; ".join(errors),
                finished=True,
            )
            return

        self.store.update_run_sync_fields(run_id=run_id, sync_status="succeeded")
        self.store.update_asset_sync_job(job_id, status="succeeded", steps=steps, finished=True)

    def start_rerun_sync_async(self, *, job_id: str, run_id: str) -> None:
        thread = threading.Thread(
            target=self.sync_rerun_job,
            kwargs={"job_id": job_id, "run_id": run_id},
            daemon=True,
        )
        thread.start()

    def _finish_rerun_sync_success(
        self,
        *,
        run_id: str,
        job_id: str,
        sync_job_id: str,
        steps: list[dict[str, Any]],
    ) -> None:
        self.store.update_run_rerun_fields(run_id=run_id, rerun_status="running")
        self.store.update_run_rerun_job(job_id, status="running")
        self.store.update_asset_sync_job(sync_job_id, status="succeeded", steps=steps, finished=True)

    def _finish_rerun_sync_failure(
        self,
        *,
        run_id: str,
        job_id: str,
        sync_job_id: str,
        steps: list[dict[str, Any]],
        errors: list[str],
    ) -> None:
        error_text = "; ".join(errors)
        self.store.update_run_rerun_fields(run_id=run_id, rerun_status="failed")
        self.store.update_run_rerun_job(job_id, status="failed", error_text=error_text, finished=True)
        self.store.update_asset_sync_job(
            sync_job_id,
            status="failed",
            steps=steps,
            error_text=error_text,
            finished=True,
        )

    def sync_rerun_job(self, *, job_id: str, run_id: str) -> None:
        rerun_job = self.store.get_run_rerun_job(job_id)
        if not rerun_job:
            raise RuntimeError("rerun job not found")
        run = self.store.get_run(run_id)
        if not run:
            raise RuntimeError("run not found")
        manifest = dict(run.get("sync_manifest") or {})
        template = self.store.get_task_template(str(run["template_id"]))
        executor_config = dict((template or {}).get("executor_config") or {})
        worker_shards = dict(rerun_job["worker_shards"])
        worker_ids = list(worker_shards.keys())
        steps = initial_worker_steps(worker_ids)
        sync_job_id = new_id("sync")
        self.store.update_run_rerun_job(job_id, status="running", sync_job_id=sync_job_id)
        self.store.create_asset_sync_job(job_id=sync_job_id, run_id=run_id, steps=steps)
        self.store.update_asset_sync_job(sync_job_id, status="running", steps=steps)

        if not rerun_needs_asset_sync(manifest):
            dataset_by_worker = dict(executor_config.get("datasetPathByWorker") or {})
            missing_workers = [
                worker_id
                for worker_id in worker_ids
                if not str(dataset_by_worker.get(worker_id) or "").strip()
            ]
            if missing_workers:
                for worker_id in worker_ids:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "failed")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "failed")
                self._finish_rerun_sync_failure(
                    run_id=run_id,
                    job_id=job_id,
                    sync_job_id=sync_job_id,
                    steps=steps,
                    errors=[
                        "missing datasetPathByWorker for workers without sync_manifest: "
                        + ", ".join(missing_workers)
                    ],
                )
                return
            for worker_id in worker_ids:
                steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                self.store.promote_worker_batches_to_queued(run_id=run_id, worker_id=worker_id)
            self._finish_rerun_sync_success(
                run_id=run_id,
                job_id=job_id,
                sync_job_id=sync_job_id,
                steps=steps,
            )
            return

        errors: list[str] = []
        lock = threading.Lock()

        def worker_thread(worker_id: str) -> None:
            nonlocal steps
            case_ids = list(worker_shards[worker_id])
            base_entry = dict((manifest.get("workers") or {}).get(worker_id) or {})
            entry = {**base_entry, "caseIds": case_ids}
            try:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "running")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self._sync_cases(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "succeeded")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "running")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self._sync_bitfun(entry, manifest)
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "succeeded")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                uv_binary = str((executor_config.get("uvBinaryByWorker") or {}).get(worker_id) or "")
                paths = worker_executor_paths(
                    target_root=str(entry["targetRoot"]),
                    uv_binary=uv_binary,
                )
                self.store.update_task_template_executor_config(
                    str(run["template_id"]),
                    {
                        "datasetPathByWorker": {worker_id: paths["datasetPath"]},
                        "mountsByWorker": {worker_id: paths["mounts"]},
                    },
                )
                self.store.promote_worker_batches_to_queued(run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                with lock:
                    steps = set_worker_step_status(steps, worker_id, "sync_cases", "failed")
                    steps = set_worker_step_status(steps, worker_id, "sync_bitfun", "failed")
                    self.store.update_asset_sync_job(sync_job_id, steps=steps)
                self.store.mark_worker_batches_sync_failed(run_id=run_id, worker_id=worker_id)
                errors.append(f"{worker_id}: {exc}")

        threads = [threading.Thread(target=worker_thread, args=(worker_id,), daemon=True) for worker_id in worker_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            self._finish_rerun_sync_failure(
                run_id=run_id,
                job_id=job_id,
                sync_job_id=sync_job_id,
                steps=steps,
                errors=errors,
            )
            return

        self._finish_rerun_sync_success(
            run_id=run_id,
            job_id=job_id,
            sync_job_id=sync_job_id,
            steps=steps,
        )

    def _sync_cases(self, entry: dict[str, Any], manifest: dict[str, Any]) -> None:
        dataset_path = Path(str(manifest["datasetPath"]))
        case_ids = list(entry["caseIds"])
        target_root = str(entry["targetRoot"])
        if entry["transport"] == "local":
            sync_cases_local(
                dataset_path=dataset_path,
                case_ids=case_ids,
                target_dataset_dir=Path(target_root) / "dataset",
            )
            return
        sync_cases_remote(
            ssh=self.ssh,
            host_alias=str(entry["sshHostAlias"]),
            dataset_path=dataset_path,
            case_ids=case_ids,
            target_root=target_root,
        )

    def _sync_bitfun(self, entry: dict[str, Any], manifest: dict[str, Any]) -> None:
        target_root = str(entry["targetRoot"])
        if entry["transport"] == "local":
            sync_bitfun_local(
                bitfun_cli_path=Path(str(manifest["bitfunCliPath"])),
                bitfun_config_dir=Path(str(manifest["bitfunConfigDir"])),
                target_bitfun_dir=Path(target_root) / "bitfun",
            )
            return
        sync_bitfun_remote(
            ssh=self.ssh,
            host_alias=str(entry["sshHostAlias"]),
            bitfun_cli_path=Path(str(manifest["bitfunCliPath"])),
            bitfun_config_dir=Path(str(manifest["bitfunConfigDir"])),
            target_root=target_root,
        )

    def cleanup_run_sync_assets(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return
        manifest = dict(run.get("sync_manifest") or {})
        workers = manifest.get("workers") or {}
        if not workers:
            return
        self.store.update_run_sync_fields(run_id=run_id, sync_status="cleaning")
        for worker_id, entry in workers.items():
            target_root = str(entry["targetRoot"])
            try:
                if entry.get("transport") == "local":
                    cleanup_sync_target_local(Path(target_root))
                else:
                    cleanup_sync_target_remote(
                        ssh=self.ssh,
                        host_alias=str(entry["sshHostAlias"]),
                        target_root=target_root,
                    )
            except Exception as exc:
                self._log(f"cleanup warning for {worker_id}: {exc}\n")
        self.store.update_run_sync_fields(run_id=run_id, sync_status="cleaned")
