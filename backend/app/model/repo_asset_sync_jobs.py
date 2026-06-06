from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import AssetSyncJob


def create_job(session: Session, *, run_id: str, steps: list[Any]) -> AssetSyncJob:
    job = AssetSyncJob(
        job_id=new_id("sync"), run_id=run_id, status="pending", steps=steps,
        log_text="", created_at=now_iso(),
    )
    session.add(job)
    return job


def get_job(session: Session, job_id: str) -> AssetSyncJob | None:
    return session.get(AssetSyncJob, job_id)


def update_job(session: Session, job_id: str, *, status: str | None = None,
               current_step: str | None = None, steps: list[Any] | None = None,
               log_append: str | None = None, error_text: str | None = None,
               finished_at: str | None = None) -> None:
    job = session.get(AssetSyncJob, job_id)
    if job is None:
        return
    if status is not None:
        job.status = status
    if current_step is not None:
        job.current_step = current_step
    if steps is not None:
        job.steps = steps
    if log_append:
        job.log_text = (job.log_text or "") + log_append
    if error_text is not None:
        job.error_text = error_text
    if finished_at is not None:
        job.finished_at = finished_at
