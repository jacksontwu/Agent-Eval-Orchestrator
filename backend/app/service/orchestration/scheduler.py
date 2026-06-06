from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from app.model import repo_batches, repo_workers


def assign_once(session: Session) -> int:
    queued = repo_batches.list_by_status(session, "queued")
    if not queued:
        return 0

    free: dict[str, int] = {}
    weight: dict[str, float] = {}
    for worker in repo_workers.list_workers(session, only_enabled=True):
        if worker.status != "online":
            continue
        available = worker.slots_total - worker.slots_used
        if available > 0:
            free[worker.worker_id] = available
            weight[worker.worker_id] = worker.allocation_weight

    assigned = 0
    for batch in queued:
        candidates = [wid for wid, slots in free.items() if slots > 0]
        if not candidates:
            break
        # Prefer the batch's preferred worker if it still has a free slot.
        if batch.preferred_worker_id in candidates:
            chosen = batch.preferred_worker_id
        else:
            chosen = max(candidates, key=lambda wid: (weight[wid], wid))
        repo_batches.assign(session, batch.batch_id, chosen)
        free[chosen] -= 1
        assigned += 1
    return assigned


def run_loop(stop_event: threading.Event, session_factory: Callable[[], AbstractContextManager[Session]],
             interval: float = 5.0) -> None:
    while not stop_event.is_set():
        try:
            with session_factory() as session:
                assign_once(session)
        except Exception:
            pass
        stop_event.wait(interval)
