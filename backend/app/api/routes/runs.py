from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.batches import BatchRead
from app.schema.runs import CreateDistributeRequest, CreateDistributeResponse, RunRead
from app.service import run_service

router = APIRouter()


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
