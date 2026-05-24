from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_eval_orchestrator.storage.store import Store

DEFAULT_TUNNEL_REMOTE_PORT = 17380
DEFAULT_UV_BIN = "/home/djn/worker/.local/bin/uv"
DEFAULT_AEO_DIR = "/home/djn/worker/agent-eval-orchestrator"
DEFAULT_HARBOR_DIR = "/home/djn/worker/harbor"
DEFAULT_WORKER_LOG_DIR = "/home/djn/worker/logs"

_RE_DJN_PASSWORD = re.compile(r"(DJN_PASSWORD=')([^']*)(')")
_RE_AEO_TOKEN = re.compile(r"(AEO_TOKEN=)(\S+)")
_RE_AUTH_TOKEN_FLAG = re.compile(r"(--auth-token\s+)(\S+)")


def redact_sensitive_log(text: str) -> str:
    text = _RE_DJN_PASSWORD.sub(r"\1***REDACTED***\3", text)
    text = _RE_AEO_TOKEN.sub(r"\1***REDACTED***", text)
    text = _RE_AUTH_TOKEN_FLAG.sub(r"\1***REDACTED***", text)
    return text


def build_bootstrap_command(*, djn_password: str) -> str:
    escaped = djn_password.replace("'", "'\"'\"'")
    return f"DJN_PASSWORD='{escaped}' bash /tmp/aeo-bootstrap.sh --yes"


def build_verify_layout_command() -> str:
    return (
        f"test -d {DEFAULT_HARBOR_DIR} && "
        f"test -d {DEFAULT_AEO_DIR} && "
        f"{DEFAULT_UV_BIN} --version"
    )


def build_daemon_start_command(
    *,
    worker_id: str,
    display_name: str,
    slots: int,
    controller_url: str,
    auth_token: str,
) -> str:
    local_root = f"{DEFAULT_AEO_DIR}/runtime/workers/{worker_id}/local"
    log_path = f"{DEFAULT_WORKER_LOG_DIR}/daemon-{worker_id}.log"
    return (
        f"mkdir -p {DEFAULT_WORKER_LOG_DIR} && "
        f"cd {DEFAULT_AEO_DIR} && "
        f"setsid {DEFAULT_UV_BIN} run python -u -m agent_eval_orchestrator.worker.daemon "
        f'--controller-url "{controller_url}" '
        f'--worker-id "{worker_id}" '
        f'--display-name "{display_name}" '
        f'--host "$(hostname -f || hostname)" '
        f"--shared-root {DEFAULT_AEO_DIR}/runtime "
        f'--local-root "{local_root}" '
        f"--slots {slots} "
        f"--poll-interval 3 "
        f'--auth-token "{auth_token}" '
        f'>> "{log_path}" 2>&1 &'
    )


STEP_LABELS = {
    "validate_ssh": "校验 SSH 连接",
    "bootstrap": "Bootstrap 系统环境",
    "verify_layout": "校验 Worker 目录结构",
    "establish_tunnel": "建立反向隧道",
    "start_daemon": "启动 Worker Daemon",
    "wait_register": "等待 Worker 注册",
}

FRESH_STEP_IDS = [
    "validate_ssh",
    "bootstrap",
    "verify_layout",
    "establish_tunnel",
    "start_daemon",
    "wait_register",
]

JOIN_STEP_IDS = [
    "validate_ssh",
    "verify_layout",
    "establish_tunnel",
    "start_daemon",
    "wait_register",
]

TUNNEL_STEP_ID = "establish_tunnel"


def initial_steps_for_mode(mode: str, *, connection_mode: str = "direct") -> list[dict[str, str]]:
    ids = FRESH_STEP_IDS if mode == "fresh" else JOIN_STEP_IDS
    if connection_mode == "direct":
        ids = [step_id for step_id in ids if step_id != TUNNEL_STEP_ID]
    return [{"id": step_id, "label": STEP_LABELS[step_id], "status": "pending"} for step_id in ids]


def set_step_status(
    steps: list[dict[str, str]],
    step_id: str,
    status: str,
) -> list[dict[str, str]]:
    updated: list[dict[str, str]] = []
    for step in steps:
        item = dict(step)
        if item["id"] == step_id:
            item["status"] = status
        updated.append(item)
    return updated


class TunnelManager:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, dict[str, object]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_record(self, worker_id: str) -> dict[str, object] | None:
        return self._load().get(worker_id)

    def save_record(self, worker_id: str, record: dict[str, object]) -> None:
        payload = self._load()
        payload[worker_id] = record
        self._save(payload)

    def remove_record(self, worker_id: str) -> dict[str, object] | None:
        payload = self._load()
        removed = payload.pop(worker_id, None)
        self._save(payload)
        return removed

    def kill_tunnel(self, worker_id: str) -> None:
        record = self.remove_record(worker_id)
        if not record:
            return
        pid = record.get("sshPid")
        if isinstance(pid, int) and pid > 0:
            import os
            import signal

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


class Provisioner:
    def __init__(
        self,
        *,
        store: Store,
        ssh_config_path: Path,
        auth_token: str | None,
        controller_port: int,
        bootstrap_script_path: Path,
        tunnel_state_path: Path,
    ) -> None:
        self.store = store
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self.auth_token = auth_token or ""
        self.controller_port = controller_port
        self.bootstrap_script_path = bootstrap_script_path
        self.tunnels = TunnelManager(tunnel_state_path)
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()
        self._current_job_id = ""

    def initial_steps(self, mode: str, *, connection_mode: str = "direct") -> list[dict[str, str]]:
        return initial_steps_for_mode(mode, connection_mode=connection_mode)

    def start_job_async(self, **kwargs: Any) -> None:
        job_id = str(kwargs["job_id"])
        thread = threading.Thread(target=self.run_job, kwargs=kwargs, daemon=True)
        self._threads[job_id] = thread
        thread.start()

    def decommission_worker(
        self,
        *,
        worker_id: str,
        ssh_host_alias: str | None,
        connection_mode: str = "tunnel",
    ) -> dict[str, object]:
        if not ssh_host_alias:
            return {"remoteCleanup": "skipped", "warnings": []}
        warnings: list[str] = []
        if connection_mode == "tunnel":
            try:
                self.tunnels.kill_tunnel(worker_id)
            except Exception as exc:
                warnings.append(f"failed to kill tunnel: {exc}")
        remote_cmd = f"pkill -f 'worker.daemon.*--worker-id {worker_id}' || true"
        try:
            result = self._ssh_run(
                ssh_host_alias,
                remote_cmd,
                check=False,
                connect_timeout_sec=10,
            )
            if result.returncode != 0 and result.stderr.strip():
                warnings.append(f"ssh pkill failed: {result.stderr.strip()}")
        except Exception as exc:
            warnings.append(f"ssh pkill failed: {exc}")
        remote_cleanup = "partial" if warnings else "done"
        return {"remoteCleanup": remote_cleanup, "warnings": warnings}

    def cancel_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        ssh_host_alias: str,
        connection_mode: str = "tunnel",
    ) -> None:
        self._cancelled.add(job_id)
        self.decommission_worker(
            worker_id=worker_id,
            ssh_host_alias=ssh_host_alias or None,
            connection_mode=connection_mode,
        )
        self.store.update_provision_job(job_id, status="cancelled", finished=True)

    def run_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        mode: str,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
        djn_password: str | None,
        connection_mode: str = "direct",
        controller_internal_ip: str | None = None,
        tunnel_remote_port: int | None = None,
        display_name: str,
        slots_total: int,
    ) -> None:
        self._current_job_id = job_id
        steps = self.initial_steps(mode, connection_mode=connection_mode)
        self.store.update_provision_job(job_id, status="running", steps=steps)

        try:
            steps = self._run_step(
                job_id,
                steps,
                "validate_ssh",
                lambda: self._validate_ssh(mode, ssh_host_alias, ssh_bootstrap_host_alias),
            )
            if mode == "fresh":
                steps = self._run_step(
                    job_id,
                    steps,
                    "bootstrap",
                    lambda: self._bootstrap(ssh_bootstrap_host_alias or "", djn_password or ""),
                )
            steps = self._run_step(
                job_id, steps, "verify_layout", lambda: self._verify_layout(ssh_host_alias)
            )
            if connection_mode == "tunnel":
                steps = self._run_step(
                    job_id,
                    steps,
                    "establish_tunnel",
                    lambda: self._establish_tunnel(
                        worker_id, ssh_host_alias, tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT
                    ),
                )
            controller_url = (
                f"http://{controller_internal_ip}:{self.controller_port}"
                if connection_mode == "direct"
                else f"http://127.0.0.1:{tunnel_remote_port or DEFAULT_TUNNEL_REMOTE_PORT}"
            )
            steps = self._run_step(
                job_id,
                steps,
                "start_daemon",
                lambda: self._start_daemon(
                    ssh_host_alias,
                    worker_id=worker_id,
                    display_name=display_name,
                    slots_total=slots_total,
                    controller_url=controller_url,
                ),
            )
            steps = self._run_step(
                job_id,
                steps,
                "wait_register",
                lambda: self._wait_for_register(worker_id),
            )
            if connection_mode == "direct":
                from agent_eval_orchestrator.controller.ssh_config import resolve_ssh_alias

                entry = resolve_ssh_alias(self.ssh_config_path, ssh_host_alias)
                self.store.update_worker_host(worker_id, entry.hostname)
            self.store.set_worker_provision_status(worker_id, provision_status="ready")
            self.store.update_provision_job(job_id, status="succeeded", steps=steps, finished=True)
        except Exception as exc:
            self.store.set_worker_provision_status(
                worker_id,
                provision_status="failed",
                last_provision_error=str(exc),
            )
            self.store.update_provision_job(
                job_id,
                status="cancelled" if job_id in self._cancelled else "failed",
                steps=steps,
                error_text=str(exc),
                finished=True,
            )
            if connection_mode == "tunnel":
                self.tunnels.kill_tunnel(worker_id)

    def _run_step(
        self,
        job_id: str,
        steps: list[dict[str, str]],
        step_id: str,
        fn: Callable[[], None],
    ) -> list[dict[str, str]]:
        if job_id in self._cancelled:
            raise RuntimeError("provision job cancelled")
        steps = set_step_status(steps, step_id, "running")
        self.store.update_provision_job(job_id, current_step=step_id, steps=steps)
        fn()
        return set_step_status(steps, step_id, "succeeded")

    def _log(self, chunk: str) -> None:
        self.store.append_provision_log(self._current_job_id, redact_sensitive_log(chunk))

    def _ssh_base(self) -> list[str]:
        return ["ssh", "-F", str(self.ssh_config_path), "-o", "BatchMode=yes"]

    def _ssh_run(
        self,
        host_alias: str,
        remote_command: str,
        *,
        check: bool = True,
        connect_timeout_sec: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [*self._ssh_base()]
        if connect_timeout_sec is not None:
            cmd.extend(["-o", f"ConnectTimeout={connect_timeout_sec}"])
        cmd.extend([host_alias, remote_command])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
        return result

    def _validate_ssh(
        self,
        mode: str,
        ssh_host_alias: str,
        ssh_bootstrap_host_alias: str | None,
    ) -> None:
        from agent_eval_orchestrator.controller.ssh_config import test_ssh_alias

        ok, message = test_ssh_alias(self.ssh_config_path, ssh_host_alias)
        if not ok:
            raise RuntimeError(message)
        if mode == "fresh":
            if not ssh_bootstrap_host_alias:
                raise RuntimeError("sshBootstrapHostAlias is required for fresh mode")
            ok_root, root_message = test_ssh_alias(self.ssh_config_path, ssh_bootstrap_host_alias)
            if not ok_root:
                raise RuntimeError(root_message)

    def _bootstrap(self, bootstrap_alias: str, djn_password: str) -> None:
        scp_cmd = [
            "scp",
            "-F",
            str(self.ssh_config_path),
            "-o",
            "BatchMode=yes",
            str(self.bootstrap_script_path),
            f"{bootstrap_alias}:/tmp/aeo-bootstrap.sh",
        ]
        result = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "scp bootstrap script failed")
        remote = build_bootstrap_command(djn_password=djn_password)
        self._ssh_run(bootstrap_alias, remote)

    def _verify_layout(self, ssh_host_alias: str) -> None:
        result = self._ssh_run(ssh_host_alias, build_verify_layout_command())
        if "uv" not in (result.stdout or "").lower():
            raise RuntimeError(
                "Worker layout verification failed. Missing harbor/agent-eval-orchestrator or uv. "
                "Try Fresh mode if this host was never bootstrapped."
            )

    def _establish_tunnel(
        self,
        worker_id: str,
        ssh_host_alias: str,
        tunnel_remote_port: int,
    ) -> None:
        cmd = [
            *self._ssh_base(),
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-f",
            "-N",
            "-R",
            f"{tunnel_remote_port}:127.0.0.1:{self.controller_port}",
            ssh_host_alias,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to start reverse tunnel")
        pid = self._find_tunnel_pid(
            ssh_host_alias=ssh_host_alias,
            tunnel_remote_port=tunnel_remote_port,
        )
        self.tunnels.save_record(
            worker_id,
            {
                "djnHostAlias": ssh_host_alias,
                "remotePort": tunnel_remote_port,
                "localPort": self.controller_port,
                "sshPid": pid,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _find_tunnel_pid(self, *, ssh_host_alias: str, tunnel_remote_port: int) -> int:
        pattern = f"127.0.0.1:{self.controller_port}"
        result = subprocess.run(
            ["pgrep", "-nf", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0
        return int(result.stdout.strip().splitlines()[0].split()[0])

    def _start_daemon(
        self,
        ssh_host_alias: str,
        *,
        worker_id: str,
        display_name: str,
        slots_total: int,
        controller_url: str,
    ) -> None:
        remote = (
            f"AEO_TOKEN={self.auth_token} "
            + build_daemon_start_command(
                worker_id=worker_id,
                display_name=display_name,
                slots=slots_total,
                controller_url=controller_url,
                auth_token=self.auth_token,
            )
        )
        self._ssh_run(ssh_host_alias, remote)

    def _wait_for_register(self, worker_id: str, *, timeout_sec: int = 90) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._current_job_id in self._cancelled:
                raise RuntimeError("provision job cancelled")
            workers = self.store.list_workers()
            match = next((item for item in workers if item["worker_id"] == worker_id), None)
            if match and match.get("last_heartbeat_at") and match.get("status") == "online":
                return
            time.sleep(2)
        raise RuntimeError(
            f"Worker did not register within {timeout_sec}s. "
            f"Check remote log /home/djn/worker/logs/daemon-{worker_id}.log"
        )
