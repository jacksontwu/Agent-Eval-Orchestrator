from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_eval_orchestrator.controller.provisioner import Provisioner
    from agent_eval_orchestrator.storage.store import Store

from agent_eval_orchestrator.controller.provisioner import (
    DEFAULT_TUNNEL_REMOTE_PORT,
    DEFAULT_UV_BIN,
    DEFAULT_WORKER_LOG_DIR,
    build_daemon_start_command,
    redact_sensitive_log,
    set_step_status,
)
from agent_eval_orchestrator.controller.ssh_runner import SshRunner
from agent_eval_orchestrator.core.worker_paths import (
    default_harbor_repo_from_shared_root,
    default_uv_binary_from_shared_root,
    repo_root_from_shared_root,
    workspace_root_from_shared_root,
)

UPDATE_STEP_LABELS = {
    "validate_ssh": "校验 SSH 连接",
    "stop_daemon": "停止 Worker Daemon",
    "pull_aeo": "更新 AEO 代码",
    "sync_aeo": "同步 AEO 依赖 (uv sync)",
    "pull_harbor": "更新 Harbor 代码",
    "restart_daemon": "重启 Worker Daemon",
    "wait_register": "等待 Worker 注册",
}

ALWAYS_STEP_IDS = ["validate_ssh", "stop_daemon"]
TAIL_STEP_IDS = ["restart_daemon", "wait_register"]


def initial_update_step_ids(targets: list[str]) -> list[str]:
    ids = list(ALWAYS_STEP_IDS)
    if "aeo" in targets:
        ids.extend(["pull_aeo", "sync_aeo"])
    if "harbor" in targets:
        ids.append("pull_harbor")
    ids.extend(TAIL_STEP_IDS)
    return ids


class WorkerUpdater:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        auth_token: str,
        controller_port: int,
        provisioner: Provisioner,
    ) -> None:
        self.store = store
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self.auth_token = auth_token
        self.controller_port = controller_port
        self.provisioner = provisioner
        self.ssh = SshRunner(self.ssh_config_path, log_fn=self._log)
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()
        self._current_job_id = ""

    def initial_steps(self, targets: list[str]) -> list[dict[str, str]]:
        return [
            {"id": step_id, "label": UPDATE_STEP_LABELS[step_id], "status": "pending"}
            for step_id in initial_update_step_ids(targets)
        ]

    def resolve_paths(self, worker: dict[str, Any]) -> dict[str, str]:
        capabilities = worker.get("capabilities") or {}
        shared_root = str(capabilities.get("sharedRoot") or "").strip()
        if not shared_root:
            raise RuntimeError("worker capabilities.sharedRoot is missing")

        aeo_repo = repo_root_from_shared_root(shared_root)
        if not aeo_repo:
            raise RuntimeError(f"cannot derive aeo repo from sharedRoot: {shared_root}")

        harbor_repo = default_harbor_repo_from_shared_root(shared_root)
        uv_bin = default_uv_binary_from_shared_root(shared_root)
        workspace = workspace_root_from_shared_root(shared_root)
        log_dir = str(workspace / "logs") if workspace else DEFAULT_WORKER_LOG_DIR

        return {
            "aeo_dir": str(aeo_repo),
            "harbor_dir": str(harbor_repo) if harbor_repo else "",
            "uv_bin": str(uv_bin) if uv_bin else DEFAULT_UV_BIN,
            "shared_root": str(Path(shared_root).expanduser()),
            "log_dir": log_dir,
        }

    def start_job_async(self, **kwargs: Any) -> None:
        job_id = str(kwargs["job_id"])
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        self._threads[job_id] = thread
        thread.start()

    def cancel_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        ssh_host_alias: str,
        connection_mode: str = "tunnel",
    ) -> None:
        self._cancelled.add(job_id)
        self.provisioner.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias or None,
            connection_mode=connection_mode,
        )
        self.store.update_worker_update_job(job_id, status="cancelled", finished=True)

    def run_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        targets: list[str],
        ssh_host_alias: str,
        connection_mode: str,
        controller_internal_ip: str | None,
        tunnel_remote_port: int | None,
        display_name: str,
        slots_total: int,
        worker: dict[str, Any],
    ) -> None:
        self._current_job_id = job_id
        steps = self.initial_steps(targets)
        self.store.update_worker_update_job(job_id, status="running", steps=steps)
        paths = self.resolve_paths(worker)

        try:
            steps = self._run_step(
                job_id,
                steps,
                "validate_ssh",
                lambda: self._validate_ssh(ssh_host_alias),
            )
            steps = self._run_step(
                job_id,
                steps,
                "stop_daemon",
                lambda: self._stop_daemon(
                    worker_id=worker_id,
                    ssh_host_alias=ssh_host_alias,
                    connection_mode=connection_mode,
                ),
            )
            if "aeo" in targets:
                steps = self._run_step(
                    job_id,
                    steps,
                    "pull_aeo",
                    lambda: self._git_pull(ssh_host_alias, paths["aeo_dir"]),
                )
                steps = self._run_step(
                    job_id,
                    steps,
                    "sync_aeo",
                    lambda: self._uv_sync(ssh_host_alias, paths["aeo_dir"], paths["uv_bin"]),
                )
            if "harbor" in targets:
                harbor_dir = paths["harbor_dir"]
                if not harbor_dir:
                    raise RuntimeError("cannot derive harbor repo from sharedRoot")
                steps = self._run_step(
                    job_id,
                    steps,
                    "pull_harbor",
                    lambda: self._git_pull(ssh_host_alias, harbor_dir),
                )
            if connection_mode == "tunnel":
                steps = self._run_step(
                    job_id,
                    steps,
                    "restart_daemon",
                    lambda: self._ensure_tunnel_and_restart(
                        worker_id=worker_id,
                        ssh_host_alias=ssh_host_alias,
                        tunnel_remote_port=tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT,
                        display_name=display_name,
                        slots_total=slots_total,
                        controller_url=f"http://127.0.0.1:{tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT}",
                        paths=paths,
                    ),
                )
            else:
                controller_url = f"http://{controller_internal_ip}:{self.controller_port}"
                steps = self._run_step(
                    job_id,
                    steps,
                    "restart_daemon",
                    lambda: self._restart_daemon(
                        ssh_host_alias=ssh_host_alias,
                        worker_id=worker_id,
                        display_name=display_name,
                        slots_total=slots_total,
                        controller_url=controller_url,
                        paths=paths,
                    ),
                )
            steps = self._run_step(
                job_id,
                steps,
                "wait_register",
                lambda: self.provisioner._wait_for_register(worker_id),
            )
            self.store.update_worker_update_job(job_id, status="succeeded", steps=steps, finished=True)
        except Exception as exc:
            self.store.update_worker_update_job(
                job_id,
                status="cancelled" if job_id in self._cancelled else "failed",
                steps=steps,
                error_text=str(exc),
                finished=True,
            )

    def _validate_ssh(self, ssh_host_alias: str) -> None:
        from agent_eval_orchestrator.controller.ssh_config import test_ssh_alias

        ok, message = test_ssh_alias(self.ssh_config_path, ssh_host_alias)
        if not ok:
            raise RuntimeError(message)

    def _stop_daemon(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str,
        connection_mode: str,
    ) -> None:
        result = self.provisioner.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias,
            connection_mode=connection_mode,
        )
        warnings = result.get("warnings") or []
        if warnings:
            self._log("\n".join(str(item) for item in warnings) + "\n")

    def _git_pull(self, ssh_host_alias: str, repo_dir: str) -> None:
        result = self.ssh.ssh_run(ssh_host_alias, f"cd {repo_dir} && git pull")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"git pull failed in {repo_dir}: {detail}")

    def _uv_sync(self, ssh_host_alias: str, aeo_dir: str, uv_bin: str) -> None:
        result = self.ssh.ssh_run(ssh_host_alias, f"cd {aeo_dir} && {uv_bin} sync")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"uv sync failed in {aeo_dir}: {detail}")

    def _restart_daemon(
        self,
        *,
        ssh_host_alias: str,
        worker_id: str,
        display_name: str,
        slots_total: int,
        controller_url: str,
        paths: dict[str, str],
    ) -> None:
        remote = build_daemon_start_command(
            worker_id=worker_id,
            display_name=display_name,
            slots=slots_total,
            controller_url=controller_url,
            auth_token=self.auth_token,
            aeo_dir=paths["aeo_dir"],
            uv_bin=paths["uv_bin"],
            log_dir=paths["log_dir"],
        )
        self.ssh.ssh_run(ssh_host_alias, remote, detach=True)

    def _ensure_tunnel_and_restart(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str,
        tunnel_remote_port: int,
        display_name: str,
        slots_total: int,
        controller_url: str,
        paths: dict[str, str],
    ) -> None:
        if not self.provisioner.tunnels.get_record(worker_id):
            self.provisioner._establish_tunnel(worker_id, ssh_host_alias, tunnel_remote_port)
        self._restart_daemon(
            ssh_host_alias=ssh_host_alias,
            worker_id=worker_id,
            display_name=display_name,
            slots_total=slots_total,
            controller_url=controller_url,
            paths=paths,
        )

    def _log(self, chunk: str) -> None:
        if self._current_job_id:
            self.store.append_worker_update_log(self._current_job_id, redact_sensitive_log(chunk))

    def _run_step(
        self,
        job_id: str,
        steps: list[dict[str, str]],
        step_id: str,
        fn: Callable[[], None],
    ) -> list[dict[str, str]]:
        if job_id in self._cancelled:
            raise RuntimeError("update job cancelled")
        steps = set_step_status(steps, step_id, "running")
        self.store.update_worker_update_job(job_id, current_step=step_id, steps=steps)
        fn()
        return set_step_status(steps, step_id, "succeeded")
