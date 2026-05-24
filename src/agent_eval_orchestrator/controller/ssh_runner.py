from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable


class SshRunner:
    def __init__(self, ssh_config_path: Path, *, log_fn: Callable[[str], None] | None = None) -> None:
        self.ssh_config_path = ssh_config_path.expanduser().resolve()
        self._log_fn = log_fn

    def _log(self, chunk: str) -> None:
        if self._log_fn and chunk:
            self._log_fn(chunk)

    def ssh_base(self) -> list[str]:
        return ["ssh", "-F", str(self.ssh_config_path), "-o", "BatchMode=yes"]

    def ssh_run(
        self,
        host_alias: str,
        remote_command: str,
        *,
        check: bool = True,
        connect_timeout_sec: int | None = None,
        detach: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [*self.ssh_base()]
        if detach:
            cmd.append("-n")
        if connect_timeout_sec is not None:
            cmd.extend(["-o", f"ConnectTimeout={connect_timeout_sec}"])
        cmd.extend([host_alias, remote_command])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
        return result

    def scp_file(self, local_path: Path, remote_target: str) -> None:
        cmd = [
            "scp",
            "-F",
            str(self.ssh_config_path),
            "-o",
            "BatchMode=yes",
            str(local_path),
            remote_target,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "scp failed")

    def rsync_dir(
        self,
        source: Path,
        destination: str,
        *,
        remote: bool,
    ) -> None:
        source_arg = f"{source}/" if source.is_dir() else str(source)
        cmd = ["rsync", "-az"]
        if remote:
            cmd.extend(["-e", f"ssh -F {self.ssh_config_path}"])
        cmd.extend([source_arg, destination])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self._log(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "rsync failed")

    def remote_mkdir_p(self, host_alias: str, remote_path: str) -> None:
        self.ssh_run(host_alias, f"mkdir -p {remote_path}")

    def remote_rm_rf(self, host_alias: str, remote_path: str) -> None:
        self.ssh_run(host_alias, f"rm -rf {remote_path}", check=False)
