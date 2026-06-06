from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id, now_iso
from app.model.tables import Batch


def create_batch(session: Session, *, run_id: str, owner: str, executor_kind: str,
                 selected_case_ids: list[str], batch_options: dict[str, Any], batch_root: str,
                 preferred_worker_id: str | None = None, parent_batch_id: str | None = None,
                 batch_kind: str = "primary", executor_metadata: dict[str, Any] | None = None) -> Batch:
    batch = Batch(
        batch_id=new_id("batch"), run_id=run_id, owner=owner, status="queued",
        preferred_worker_id=preferred_worker_id, executor_kind=executor_kind,
        executor_metadata=executor_metadata or {}, selected_case_ids=selected_case_ids,
        batch_options=batch_options, summary={}, artifact_index={}, batch_root=batch_root,
        parent_batch_id=parent_batch_id, batch_kind=batch_kind, created_at=now_iso(),
    )
    session.add(batch)
    return batch


def get_batch(session: Session, batch_id: str) -> Batch | None:
    return session.get(Batch, batch_id)


def list_batches_for_run(session: Session, run_id: str) -> list[Batch]:
    stmt = select(Batch).where(Batch.run_id == run_id).order_by(Batch.created_at)
    return list(session.scalars(stmt))


def list_by_status(session: Session, status: str) -> list[Batch]:
    stmt = select(Batch).where(Batch.status == status).order_by(Batch.created_at)
    return list(session.scalars(stmt))


def requeue_running_for_worker(session: Session, worker_id: str) -> int:
    stmt = select(Batch).where(
        Batch.assigned_worker_id == worker_id,
        Batch.status.in_(("assigned", "running")),
    )
    count = 0
    for batch in session.scalars(stmt):
        batch.assigned_worker_id = None
        batch.status = "queued"
        batch.current_step = None
        count += 1
    return count


def next_assigned_for_worker(session: Session, worker_id: str) -> Batch | None:
    stmt = (
        select(Batch)
        .where(Batch.assigned_worker_id == worker_id, Batch.status == "assigned")
        .order_by(Batch.created_at)
        .limit(1)
    )
    return session.scalars(stmt).first()


def assign(session: Session, batch_id: str, worker_id: str) -> None:
    batch = session.get(Batch, batch_id)
    if batch is None:
        return
    batch.assigned_worker_id = worker_id
    batch.status = "assigned"
    batch.current_step = None
    batch.started_at = now_iso()


def set_status(session: Session, batch_id: str, status: str, *, current_step: str | None = None,
               error_text: str | None = None, started_at: str | None = None,
               finished_at: str | None = None) -> None:
    batch = session.get(Batch, batch_id)
    if batch is None:
        return
    batch.status = status
    if current_step is not None:
        batch.current_step = current_step
    if error_text is not None:
        batch.error_text = error_text
    if started_at is not None:
        batch.started_at = started_at
    if finished_at is not None:
        batch.finished_at = finished_at


def set_summary(session: Session, batch_id: str, summary: dict[str, Any],
                artifact_index: dict[str, Any]) -> None:
    batch = session.get(Batch, batch_id)
    if batch is None:
        return
    batch.summary = summary
    batch.artifact_index = artifact_index
