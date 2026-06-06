from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_batches, repo_case_runs, repo_rerun_jobs, repo_runs
from app.model.tables import RunRerunJob
from app.service.errors import NotFoundError, ServiceError
from app.service.status import case_error_type, case_is_errored


def _errored_case_ids(session: Session, run_id: str, selected_error_types: list[str] | None) -> list[str]:
    case_ids: list[str] = []
    wanted = set(selected_error_types) if selected_error_types else None
    for case in repo_case_runs.list_for_run(session, run_id):
        payload = {
            "status": case.status,
            "error_text": case.error_text,
            "errorType": (case.metrics or {}).get("errorType"),
            "metrics": case.metrics or {},
        }
        if not case_is_errored(payload):
            continue
        if wanted is not None and case_error_type(payload) not in wanted:
            continue
        case_ids.append(case.case_id)
    return case_ids


def _worker_shards(session: Session, run_id: str, case_ids: list[str]) -> dict[str, list[str]]:
    shards: dict[str, list[str]] = {}
    case_set = set(case_ids)
    for batch in repo_batches.list_batches_for_run(session, run_id):
        worker_id = batch.assigned_worker_id or batch.preferred_worker_id
        if not worker_id:
            continue
        for case_id in batch.selected_case_ids:
            if case_id in case_set:
                shards.setdefault(worker_id, []).append(case_id)
    return shards


def create_rerun_job(session: Session, run_id: str,
                     selected_error_types: list[str] | None = None) -> RunRerunJob:
    run = repo_runs.get_run(session, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")
    case_ids = _errored_case_ids(session, run_id, selected_error_types)
    if not case_ids:
        raise ServiceError("no errored cases to rerun")
    shards = _worker_shards(session, run_id, case_ids)
    job = repo_rerun_jobs.create_job(
        session, run_id=run_id, case_ids=case_ids, worker_shards=shards,
        selected_error_types=selected_error_types,
    )
    repo_runs.set_rerun(session, run_id, status="pending", job_id=job.job_id)
    session.commit()
    return job
