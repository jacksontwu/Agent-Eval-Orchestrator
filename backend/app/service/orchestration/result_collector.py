from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.core.layout import Layout
from app.model import repo_batches, repo_case_runs
from app.service.errors import ServiceError
from app.service.normalizers.harbor import normalize_harbor_job


def _is_subpath(target: Path, base: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _safe_extract_tar(archive: bytes, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    base = target_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (target_dir / member.name).resolve()
            if not _is_subpath(target, base):
                raise ServiceError(f"unsafe tar member: {member.name}")
        tar.extractall(target_dir)


def ingest_archive(session: Session, *, batch_id: str, sha256: str,
                   file_stream: BinaryIO, layout: Layout) -> None:
    layout.ensure_dirs()
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(delete=True) as tmp:
        for chunk in iter(lambda: file_stream.read(1 << 20), b""):
            digest.update(chunk)
            tmp.write(chunk)
        tmp.flush()
        if digest.hexdigest() != sha256:
            raise ServiceError("archive sha256 mismatch")
        tmp.seek(0)
        archive_bytes = tmp.read()

    target_dir = layout.imported_jobs_dir / batch_id
    _safe_extract_tar(archive_bytes, target_dir)

    _merge_into_batch(session, batch_id=batch_id, job_dir=target_dir)


def _merge_into_batch(session: Session, *, batch_id: str, job_dir: Path) -> None:
    batch = repo_batches.get_batch(session, batch_id)
    if batch is None:
        return
    summary, cases, artifact_index = normalize_harbor_job(job_dir, batch_id)
    rows = [
        {
            "case_id": case["caseId"],
            "status": case["status"],
            "score": case.get("score"),
            "metrics": case.get("metrics") or {},
            "artifact_index": case.get("artifactIndex") or {},
            "error_text": case.get("errorText"),
        }
        for case in cases
    ]
    repo_case_runs.replace_for_batch(session, batch_id, rows)
    repo_batches.set_summary(session, batch_id, summary, artifact_index)
    repo_batches.set_status(session, batch_id, "succeeded", finished_at=now_iso())
    session.commit()
