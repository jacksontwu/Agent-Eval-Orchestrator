from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.model import repo_batches, repo_workers


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def reap_once(session: Session, timeout_sec: float) -> int:
    now = datetime.now(timezone.utc)
    reaped = 0
    for worker in repo_workers.list_workers(session):
        if worker.status != "online":
            continue
        last = _parse_iso(worker.last_heartbeat_at)
        if last is not None and (now - last).total_seconds() <= timeout_sec:
            continue
        repo_workers.update_runtime(session, worker.worker_id, status="offline", slots_used=0)
        repo_batches.requeue_running_for_worker(session, worker.worker_id)
        reaped += 1
    return reaped


def run_loop(stop_event: threading.Event, session_factory: Callable[[], AbstractContextManager[Session]],
             interval: float = 5.0, timeout_sec: float = 45.0) -> None:
    while not stop_event.is_set():
        try:
            with session_factory() as session:
                reap_once(session, timeout_sec)
        except Exception:
            pass
        stop_event.wait(interval)
