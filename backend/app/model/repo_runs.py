from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import Run


def create_run(session: Session, *, template_id: str, owner: str, display_name: str,
               parent_run_id: str | None = None) -> Run:
    now = now_iso()
    run = Run(
        run_id=new_id("run"), template_id=template_id, owner=owner, display_name=display_name,
        parent_run_id=parent_run_id, sync_status="", sync_manifest={}, rerun_status="idle",
        created_at=now, updated_at=now,
    )
    session.add(run)
    return run


def get_run(session: Session, run_id: str) -> Run | None:
    return session.get(Run, run_id)


def list_runs(session: Session, *, owner: str | None = None) -> list[Run]:
    stmt = select(Run).order_by(Run.created_at.desc())
    if owner is not None:
        stmt = stmt.where(Run.owner == owner)
    return list(session.scalars(stmt))


def set_latest_batch(session: Session, run_id: str, batch_id: str) -> None:
    run = session.get(Run, run_id)
    if run is not None:
        run.latest_batch_id = batch_id
        run.updated_at = now_iso()


def set_sync(session: Session, run_id: str, *, status: str, job_id: str | None = None,
             manifest: dict[str, Any] | None = None) -> None:
    run = session.get(Run, run_id)
    if run is None:
        return
    run.sync_status = status
    if job_id is not None:
        run.sync_job_id = job_id
    if manifest is not None:
        run.sync_manifest = manifest
    run.updated_at = now_iso()


def set_rerun(session: Session, run_id: str, *, status: str, job_id: str | None = None) -> None:
    run = session.get(Run, run_id)
    if run is None:
        return
    run.rerun_status = status
    if job_id is not None:
        run.rerun_job_id = job_id
    run.updated_at = now_iso()
