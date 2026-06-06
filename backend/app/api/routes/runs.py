from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.batches import BatchRead
from app.schema.runs import RunRead
from app.service import run_service

router = APIRouter()


@router.get("/eval-tasks/{run_id}")
def get_run_detail(run_id: str, session: Session = Depends(db_session)) -> dict:
    run, batches = run_service.get_run_detail(session, run_id)
    payload = RunRead.model_validate(run).model_dump(by_alias=True)
    payload["batches"] = [BatchRead.model_validate(b).model_dump(by_alias=True) for b in batches]
    return payload
