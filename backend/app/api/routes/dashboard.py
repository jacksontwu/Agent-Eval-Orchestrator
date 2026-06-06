from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.batches import BatchRead
from app.schema.dashboard import DashboardTasksResponse
from app.service import dashboard_service

router = APIRouter()


@router.get("/dashboard/tasks", response_model=DashboardTasksResponse)
def dashboard_tasks(session: Session = Depends(db_session)) -> DashboardTasksResponse:
    return DashboardTasksResponse(tasks=dashboard_service.list_tasks(session))


@router.get("/dashboard/batches")
def dashboard_batches(session: Session = Depends(db_session)) -> dict:
    items = dashboard_service.list_batches(session)
    return {"batches": [BatchRead.model_validate(b).model_dump(by_alias=True) for b in items]}
