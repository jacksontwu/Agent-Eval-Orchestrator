from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_workers
from app.model.tables import Worker
from app.schema.workers import WorkerSettingsUpdate
from app.service.errors import NotFoundError


def list_workers(session: Session) -> list[Worker]:
    return repo_workers.list_workers(session)


def update_settings(session: Session, worker_id: str, data: WorkerSettingsUpdate) -> Worker:
    worker = repo_workers.get_worker(session, worker_id)
    if worker is None:
        raise NotFoundError(f"worker not found: {worker_id}")
    if data.enabled is not None:
        worker.enabled = 1 if data.enabled else 0
    if data.note is not None:
        worker.note = data.note
    if data.tags is not None:
        worker.tags = data.tags
    if data.allocation_weight is not None:
        worker.allocation_weight = data.allocation_weight
    session.commit()
    return worker


def delete_worker(session: Session, worker_id: str) -> None:
    worker = repo_workers.get_worker(session, worker_id)
    if worker is None:
        raise NotFoundError(f"worker not found: {worker_id}")
    repo_workers.delete_worker(session, worker_id)
    session.commit()
