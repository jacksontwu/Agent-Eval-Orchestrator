from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class SshHostEntry:
    host_alias: str
    hostname: str
    user: str
    port: int


_HOST_BLOCK_RE = re.compile(r"(?ms)^Host\s+(\S+)\s*\n(.*?)(?=^Host\s|\Z)")


def _parse_host_blocks(config_text: str) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    for match in _HOST_BLOCK_RE.finditer(config_text):
        alias = match.group(1).strip()
        if "*" in alias or "?" in alias or "!" in alias:
            continue
        options: dict[str, str] = {}
        for line in match.group(2).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if " " not in stripped:
                continue
            key, value = stripped.split(None, 1)
            options[key.lower()] = value.strip()
        blocks[alias] = options
    return blocks


def list_ssh_hosts(config_path: Path) -> list[dict[str, object]]:
    text = config_path.expanduser().read_text(encoding="utf-8")
    blocks = _parse_host_blocks(text)
    items: list[dict[str, object]] = []
    for alias in sorted(blocks):
        resolved = resolve_ssh_alias(config_path, alias)
        items.append(
            {
                "hostAlias": resolved.host_alias,
                "hostname": resolved.hostname,
                "user": resolved.user,
                "port": resolved.port,
            }
        )
    return items


def resolve_ssh_alias(config_path: Path, host_alias: str) -> SshHostEntry:
    config_path = config_path.expanduser().resolve()
    blocks = _parse_host_blocks(config_path.read_text(encoding="utf-8"))
    if host_alias not in blocks:
        raise ValueError(f"SSH host alias not found in config: {host_alias}")

    result = subprocess.run(
        ["ssh", "-F", str(config_path), "-G", host_alias],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"ssh -G failed for {host_alias}")

    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if " " not in line:
            continue
        key, value = line.split(" ", 1)
        parsed[key.lower()] = value.strip()

    hostname = parsed.get("hostname") or blocks[host_alias].get("hostname") or host_alias
    user = parsed.get("user") or blocks[host_alias].get("user") or ""
    port_raw = parsed.get("port") or blocks[host_alias].get("port") or "22"
    return SshHostEntry(
        host_alias=host_alias,
        hostname=hostname,
        user=user,
        port=int(port_raw),
    )


def test_ssh_alias(config_path: Path, host_alias: str, *, timeout_sec: int = 10) -> tuple[bool, str]:
    config_path = config_path.expanduser().resolve()
    try:
        resolve_ssh_alias(config_path, host_alias)
    except ValueError as exc:
        return False, str(exc)

    result = subprocess.run(
        [
            "ssh",
            "-F",
            str(config_path),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={timeout_sec}",
            host_alias,
            "echo",
            "ok",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and "ok" in (result.stdout or ""):
        return True, "connected"
    message = (result.stderr or result.stdout or "SSH connection failed").strip()
    return False, message
