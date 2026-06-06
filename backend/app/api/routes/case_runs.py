from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.case_runs import CaseRunRead
from app.service import run_service

router = APIRouter()


@router.get("/case-runs")
def list_case_runs(run_id: str = Query(alias="runId"),
                   session: Session = Depends(db_session)) -> dict:
    items = run_service.list_case_runs(session, run_id)
    return {"caseRuns": [CaseRunRead.model_validate(c).model_dump(by_alias=True) for c in items]}
