from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import now_iso
from app.model.tables import Worker


def get_worker(session: Session, worker_id: str) -> Worker | None:
    return session.get(Worker, worker_id)


def list_workers(session: Session, *, only_enabled: bool = False) -> list[Worker]:
    stmt = select(Worker).order_by(Worker.created_at)
    if only_enabled:
        stmt = stmt.where(Worker.enabled == 1)
    return list(session.scalars(stmt))


def upsert_worker(session: Session, *, worker_id: str, display_name: str, host: str,
                  slots_total: int, capabilities: dict[str, Any]) -> Worker:
    worker = session.get(Worker, worker_id)
    now = now_iso()
    if worker is None:
        worker = Worker(worker_id=worker_id, display_name=display_name, host=host,
                        slots_total=slots_total, slots_used=0, capabilities=capabilities,
                        status="online", enabled=1, note="", tags=[], allocation_weight=1.0,
                        last_heartbeat_at=now, created_at=now, updated_at=now)
        session.add(worker)
    else:
        worker.display_name = display_name
        worker.host = host
        worker.slots_total = slots_total
        worker.capabilities = capabilities
        worker.status = "online"
        worker.last_heartbeat_at = now
        worker.updated_at = now
    return worker


def update_runtime(session: Session, worker_id: str, *, slots_used: int | None = None,
                   status: str | None = None, last_heartbeat_at: str | None = None) -> None:
    worker = session.get(Worker, worker_id)
    if worker is None:
        return
    if slots_used is not None:
        worker.slots_used = slots_used
    if status is not None:
        worker.status = status
    if last_heartbeat_at is not None:
        worker.last_heartbeat_at = last_heartbeat_at
    worker.updated_at = now_iso()


def set_enabled(session: Session, worker_id: str, enabled: bool) -> None:
    worker = session.get(Worker, worker_id)
    if worker is not None:
        worker.enabled = 1 if enabled else 0
        worker.updated_at = now_iso()


def delete_worker(session: Session, worker_id: str) -> None:
    worker = session.get(Worker, worker_id)
    if worker is not None:
        session.delete(worker)
