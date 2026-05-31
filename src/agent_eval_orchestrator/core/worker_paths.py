from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def repo_root_from_shared_root(shared_root: Path | str) -> Path | None:
    shared_path = Path(shared_root).expanduser()
    if not str(shared_path).strip():
        return None
    return shared_path.parent if shared_path.name == "runtime" else shared_path


def workspace_root_from_shared_root(shared_root: Path | str) -> Path | None:
    repo_root = repo_root_from_shared_root(shared_root)
    return repo_root.parent if repo_root else None


def user_home_from_shared_root(shared_root: Path | str) -> Path | None:
    workspace = workspace_root_from_shared_root(shared_root)
    if not workspace or workspace.name != "worker":
        return None
    home = workspace.parent
    parts = home.parts
    if len(parts) >= 3 and parts[1] == "home":
        return home
    return None


def default_harbor_repo_from_shared_root(shared_root: Path | str) -> Path | None:
    workspace = workspace_root_from_shared_root(shared_root)
    return workspace / "harbor" if workspace else None


def default_uv_binary_from_shared_root(shared_root: Path | str) -> Path | None:
    home = user_home_from_shared_root(shared_root)
    if home:
        return home / ".local" / "bin" / "uv"
    workspace = workspace_root_from_shared_root(shared_root)
    if workspace:
        return workspace / ".local" / "bin" / "uv"
    return None


def is_executable(path: Path | str) -> bool:
    candidate = Path(path).expanduser()
    return candidate.is_file() and os.access(candidate, os.X_OK)


def resolve_uv_binary(
    *,
    explicit: str | None = None,
    configured: str | None = None,
    shared_root: Path | str | None = None,
) -> str:
    for candidate in (explicit, configured):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if is_executable(path):
            return str(path)

    if shared_root:
        derived = default_uv_binary_from_shared_root(shared_root)
        if derived and is_executable(derived):
            return str(derived)

    found = shutil.which("uv")
    if found:
        return found

    home_uv = Path.home() / ".local" / "bin" / "uv"
    if is_executable(home_uv):
        return str(home_uv)

    if configured and configured not in ("", "uv"):
        return configured
    return "uv"


def default_bitfun_config_dir(*, worker_id: str, shared_root: Path | str | None) -> str:
    if worker_id == "local-a":
        return "/root/.config/bitfun"
    if worker_id == "remote-a":
        return "/home/wt/.config/bitfun"
    if shared_root:
        home = user_home_from_shared_root(shared_root)
        if home:
            return str(home / ".config" / "bitfun")
    return "/root/.config/bitfun"


def build_harbor_bind_mounts(*, uv_binary: str, harbor_repo: str, bitfun_config_dir: str) -> list[dict[str, Any]]:
    harbor_root = str(Path(harbor_repo).expanduser())
    bitfun_config_root = str(Path(bitfun_config_dir).expanduser())
    return [
        {"type": "bind", "source": uv_binary, "target": "/usr/local/bin/uv", "read_only": True},
        {
            "type": "bind",
            "source": f"{harbor_root}/BitFun/target/release/bitfun-cli",
            "target": "/usr/local/bin/bitfun-cli",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": f"{bitfun_config_root}/config",
            "target": "/root/.config/bitfun/config",
            "read_only": True,
        },
    ]


def build_sync_bind_mounts(*, uv_binary: str, sync_root: str) -> list[dict[str, Any]]:
    root = str(Path(sync_root).expanduser())
    return [
        {"type": "bind", "source": uv_binary, "target": "/usr/local/bin/uv", "read_only": True},
        {
            "type": "bind",
            "source": f"{root}/bitfun/bitfun-cli",
            "target": "/usr/local/bin/bitfun-cli",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": f"{root}/bitfun/config",
            "target": "/root/.config/bitfun/config",
            "read_only": True,
        },
    ]


def resolve_harbor_repo(
    *,
    explicit: str | None = None,
    shared_root: Path | str | None = None,
    configured: str | None = None,
    default: Path | str,
) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if configured:
        candidates.append(Path(configured).expanduser())
    if shared_root:
        derived = default_harbor_repo_from_shared_root(shared_root)
        if derived:
            candidates.append(derived)
    candidates.append(Path(default).expanduser())

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        resolved = candidate.expanduser().resolve()
        if resolved.is_dir():
            return resolved

    tried = ", ".join(str(item) for item in candidates)
    raise RuntimeError(f"harbor repo not found; tried: {tried}")
