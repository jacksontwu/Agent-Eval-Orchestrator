from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_current_principal, require_permission
from app.core.permissions import PermissionCode
from app.schema.batches import BatchRead
from app.schema.runs import CreateDistributeRequest, CreateDistributeResponse, RunRead
from app.service import run_service
from app.service.auth_service import Principal
from app.service.orchestration import rerun_coordinator

router = APIRouter()


def _can_read_run(principal: Principal, owner: str) -> bool:
    return PermissionCode.TASKS_READ_ALL in principal.permissions or (
        PermissionCode.TASKS_READ_OWN in principal.permissions and owner == principal.username
    )


def _can_manage_run(principal: Principal, owner: str) -> bool:
    return PermissionCode.TASKS_MANAGE_ALL in principal.permissions or (
        PermissionCode.TASKS_MANAGE_OWN in principal.permissions and owner == principal.username
    )


def _require_read_run(principal: Principal, owner: str) -> None:
    if not _can_read_run(principal, owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="run access denied")


def _require_manage_run(principal: Principal, owner: str) -> None:
    if not _can_manage_run(principal, owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="run access denied")


@router.post("/runs/{run_id}/rerun-exceptions", status_code=status.HTTP_201_CREATED)
def rerun_exceptions(run_id: str, body: dict = Body(default={}),
                     session: Session = Depends(db_session),
                     principal: Principal = Depends(require_current_principal)) -> dict:
    run = run_service.get_run(session, run_id)
    _require_manage_run(principal, run.owner)
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
def get_run_sync(run_id: str, session: Session = Depends(db_session),
                 principal: Principal = Depends(require_current_principal)) -> dict:
    run = run_service.get_run(session, run_id)
    _require_read_run(principal, run.owner)
    return {
        "runId": run.run_id,
        "syncStatus": run.sync_status,
        "syncJobId": run.sync_job_id,
    }


@router.post("/eval-tasks/create-and-distribute", response_model=CreateDistributeResponse,
             status_code=status.HTTP_201_CREATED)
def create_and_distribute(body: CreateDistributeRequest,
                          session: Session = Depends(db_session),
                          principal: Principal = Depends(require_permission(PermissionCode.TASKS_CREATE))) -> CreateDistributeResponse:
    return run_service.create_and_distribute(session, body, owner=principal.username)


@router.get("/eval-tasks/{run_id}")
def get_run_detail(run_id: str, session: Session = Depends(db_session),
                   principal: Principal = Depends(require_current_principal)) -> dict:
    run, batches = run_service.get_run_detail(session, run_id)
    _require_read_run(principal, run.owner)
    payload = RunRead.model_validate(run).model_dump(by_alias=True)
    payload["batches"] = [BatchRead.model_validate(b).model_dump(by_alias=True) for b in batches]
    return payload
