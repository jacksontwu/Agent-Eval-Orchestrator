from __future__ import annotations

import os
import shutil
from pathlib import Path


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
    if shared_root:
        derived = default_harbor_repo_from_shared_root(shared_root)
        if derived:
            candidates.append(derived)
    if configured:
        candidates.append(Path(configured).expanduser())
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
