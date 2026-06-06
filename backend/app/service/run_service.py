from __future__ import annotations

from sqlalchemy.orm import Session

from app.model import repo_batches, repo_case_runs, repo_runs
from app.model.tables import Batch, CaseRun, Run
from app.service.errors import NotFoundError


def get_run(session: Session, run_id: str) -> Run:
    run = repo_runs.get_run(session, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")
    return run


def get_run_detail(session: Session, run_id: str) -> tuple[Run, list[Batch]]:
    run = get_run(session, run_id)
    batches = repo_batches.list_batches_for_run(session, run_id)
    return run, batches


def list_case_runs(session: Session, run_id: str) -> list[CaseRun]:
    return repo_case_runs.list_for_run(session, run_id)


def get_batch(session: Session, batch_id: str) -> Batch:
    batch = repo_batches.get_batch(session, batch_id)
    if batch is None:
        raise NotFoundError(f"batch not found: {batch_id}")
    return batch
