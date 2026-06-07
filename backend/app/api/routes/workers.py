from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.workers import WorkerRead, WorkerSettingsUpdate
from app.service import worker_service

router = APIRouter()


@router.get("/workers", dependencies=[Depends(require_permission(PermissionCode.WORKERS_READ))])
def list_workers(session: Session = Depends(db_session)) -> dict:
    items = worker_service.list_workers(session)
    return {"workers": [WorkerRead.model_validate(w).model_dump(by_alias=True) for w in items]}


@router.post(
    "/workers/{worker_id}/settings",
    response_model=WorkerRead,
    dependencies=[Depends(require_permission(PermissionCode.WORKERS_MANAGE))],
)
def update_settings(worker_id: str, body: WorkerSettingsUpdate,
                    session: Session = Depends(db_session)) -> WorkerRead:
    worker = worker_service.update_settings(session, worker_id, body)
    return WorkerRead.model_validate(worker)


@router.delete("/workers/{worker_id}", dependencies=[Depends(require_permission(PermissionCode.WORKERS_MANAGE))])
def delete_worker(worker_id: str, session: Session = Depends(db_session)) -> dict:
    worker_service.delete_worker(session, worker_id)
    return {"ok": True}
