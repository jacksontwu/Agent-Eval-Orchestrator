from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.batches import BatchRead
from app.schema.runs import CreateDistributeRequest, CreateDistributeResponse, RunRead
from app.service import run_service
from app.service.orchestration import rerun_coordinator

router = APIRouter()


@router.post("/runs/{run_id}/rerun-exceptions", status_code=status.HTTP_201_CREATED)
def rerun_exceptions(run_id: str, body: dict = Body(default={}),
                     session: Session = Depends(db_session)) -> dict:
    selected = body.get("selectedErrorTypes") or body.get("selected_error_types")
    job = rerun_coordinator.create_rerun_job(session, run_id, selected)
    return {
        "jobId": job.job_id,
        "runId": job.run_id,
        "status": job.status,
        "caseIds": job.case_ids,
        "workerShards": job.worker_shards,
    }


@router.get("/runs/{run_id}/sync")
def get_run_sync(run_id: str, session: Session = Depends(db_session)) -> dict:
    run = run_service.get_run(session, run_id)
    return {
        "runId": run.run_id,
        "syncStatus": run.sync_status,
        "syncJobId": run.sync_job_id,
    }


@router.post("/eval-tasks/create-and-distribute", response_model=CreateDistributeResponse,
             status_code=status.HTTP_201_CREATED)
def create_and_distribute(body: CreateDistributeRequest,
                          session: Session = Depends(db_session)) -> CreateDistributeResponse:
    return run_service.create_and_distribute(session, body)


@router.get("/eval-tasks/{run_id}")
def get_run_detail(run_id: str, session: Session = Depends(db_session)) -> dict:
    run, batches = run_service.get_run_detail(session, run_id)
    payload = RunRead.model_validate(run).model_dump(by_alias=True)
    payload["batches"] = [BatchRead.model_validate(b).model_dump(by_alias=True) for b in batches]
    return payload
