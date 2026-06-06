from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_batches, repo_case_runs, repo_runs
from app.model.tables import Batch
from app.schema.dashboard import DashboardTask
from app.service.status import overall_status_from_batch_counts

_BATCH_STATUS_BUCKET = {
    "running": "running",
    "assigned": "running",
    "pending_sync": "pending_sync",
    "syncing": "pending_sync",
    "failed": "failed",
    "sync_failed": "sync_failed",
    "queued": "queued",
    "succeeded": "succeeded",
}


def _status_counts(batches: list[Batch]) -> dict[str, int]:
    counts = {"running": 0, "pending_sync": 0, "failed": 0, "sync_failed": 0, "queued": 0, "succeeded": 0}
    for batch in batches:
        bucket = _BATCH_STATUS_BUCKET.get(batch.status)
        if bucket:
            counts[bucket] += 1
    return counts


def list_tasks(session: Session) -> list[DashboardTask]:
    tasks: list[DashboardTask] = []
    for run in repo_runs.list_runs(session):
        batches = repo_batches.list_batches_for_run(session, run.run_id)
        status_counts = _status_counts(batches)
        has_primary = any(b.batch_kind == "primary" for b in batches)
        overall = overall_status_from_batch_counts(status_counts, has_primary)

        case_counts: dict[str, int] = {}
        for case in repo_case_runs.list_for_run(session, run.run_id):
            case_counts[case.status] = case_counts.get(case.status, 0) + 1

        tasks.append(DashboardTask(
            run_id=run.run_id,
            display_name=run.display_name,
            owner=run.owner,
            status=overall,
            template_id=run.template_id,
            latest_batch_id=run.latest_batch_id,
            counts=case_counts,
            updated_at=run.updated_at,
        ))
    return tasks


def list_batches(session: Session) -> list[Batch]:
    from sqlalchemy import select

    return list(session.scalars(select(Batch).order_by(Batch.created_at)))
