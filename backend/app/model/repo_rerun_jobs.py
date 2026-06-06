from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import RunRerunJob


def create_job(session: Session, *, run_id: str, case_ids: list[str],
               worker_shards: dict[str, Any], selected_error_types: list[str] | None = None) -> RunRerunJob:
    job = RunRerunJob(
        job_id=new_id("rerun"), run_id=run_id, status="pending", case_ids=case_ids,
        worker_shards=worker_shards, rerun_batches=[], selected_error_types=selected_error_types,
        created_at=now_iso(),
    )
    session.add(job)
    return job


def get_job(session: Session, job_id: str) -> RunRerunJob | None:
    return session.get(RunRerunJob, job_id)


def update_job(session: Session, job_id: str, *, status: str | None = None,
               sync_job_id: str | None = None, rerun_batches: list[Any] | None = None,
               error_text: str | None = None, finished_at: str | None = None) -> None:
    job = session.get(RunRerunJob, job_id)
    if job is None:
        return
    if status is not None:
        job.status = status
    if sync_job_id is not None:
        job.sync_job_id = sync_job_id
    if rerun_batches is not None:
        job.rerun_batches = rerun_batches
    if error_text is not None:
        job.error_text = error_text
    if finished_at is not None:
        job.finished_at = finished_at
