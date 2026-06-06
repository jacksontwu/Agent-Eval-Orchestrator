from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.model import repo_batches
from app.schema.assets import AssetEntry, AssetManifest
from app.service.errors import NotFoundError, ServiceError


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _roots(meta: dict) -> tuple[Path | None, Path | None, Path | None]:
    dataset_path = Path(meta["datasetPath"]).expanduser().resolve() if meta.get("datasetPath") else None
    cli_path = Path(meta["bitfunCliPath"]).expanduser().resolve() if meta.get("bitfunCliPath") else None
    config_dir = Path(meta["bitfunConfigDir"]).expanduser().resolve() if meta.get("bitfunConfigDir") else None
    return dataset_path, cli_path, config_dir


def build_manifest(session: Session, batch_id: str) -> AssetManifest:
    batch = repo_batches.get_batch(session, batch_id)
    if batch is None:
        raise NotFoundError(f"batch not found: {batch_id}")
    meta = batch.executor_metadata or {}
    dataset_path, cli_path, config_dir = _roots(meta)
    selected_case_ids = list(batch.selected_case_ids or [])

    entries: list[AssetEntry] = []

    if dataset_path is not None and dataset_path.is_dir():
        for case_id in selected_case_ids:
            case_dir = dataset_path / case_id
            if not case_dir.is_dir():
                continue
            for file in sorted(p for p in case_dir.rglob("*") if p.is_file()):
                rel = file.relative_to(dataset_path)
                entries.append(AssetEntry(
                    path=f"cases/{rel.as_posix()}",
                    size=file.stat().st_size,
                    sha256=_sha256_file(file),
                    kind="case",
                ))

    if config_dir is not None and config_dir.is_dir():
        for file in sorted(p for p in config_dir.rglob("*") if p.is_file()):
            rel = file.relative_to(config_dir)
            entries.append(AssetEntry(
                path=f"bitfun/{rel.as_posix()}",
                size=file.stat().st_size,
                sha256=_sha256_file(file),
                kind="bitfun",
            ))

    if cli_path is not None and cli_path.is_file():
        entries.append(AssetEntry(
            path=f"cli/{cli_path.name}",
            size=cli_path.stat().st_size,
            sha256=_sha256_file(cli_path),
            kind="cli",
        ))

    return AssetManifest(
        asset_manifest_id=f"am-{batch_id}",
        target_root_rel=f"sync/{batch.run_id}",
        entries=entries,
    )


def manifest_for(session: Session, asset_manifest_id: str) -> AssetManifest:
    if not asset_manifest_id.startswith("am-"):
        raise NotFoundError(f"unknown asset manifest: {asset_manifest_id}")
    batch_id = asset_manifest_id.removeprefix("am-")
    return build_manifest(session, batch_id)


def open_entry(session: Session, asset_manifest_id: str, path: str) -> Path:
    if not asset_manifest_id.startswith("am-"):
        raise NotFoundError(f"unknown asset manifest: {asset_manifest_id}")
    batch_id = asset_manifest_id.removeprefix("am-")
    batch = repo_batches.get_batch(session, batch_id)
    if batch is None:
        raise NotFoundError(f"batch not found: {batch_id}")
    meta = batch.executor_metadata or {}
    dataset_path, cli_path, config_dir = _roots(meta)

    if path.startswith("cases/") and dataset_path is not None:
        root = dataset_path
        candidate = (dataset_path / path[len("cases/"):])
    elif path.startswith("bitfun/") and config_dir is not None:
        root = config_dir
        candidate = (config_dir / path[len("bitfun/"):])
    elif path.startswith("cli/") and cli_path is not None:
        root = cli_path.parent
        candidate = (cli_path.parent / path[len("cli/"):])
    else:
        raise NotFoundError(f"unknown asset entry: {path}")

    resolved = candidate.resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise ServiceError(f"path traversal rejected: {path}")
    if not resolved.is_file():
        raise NotFoundError(f"asset file not found: {path}")
    return resolved
