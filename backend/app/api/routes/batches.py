from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.batches import BatchRead
from app.service import run_service

router = APIRouter()


@router.get("/batches/{batch_id}", response_model=BatchRead)
def get_batch(batch_id: str, session: Session = Depends(db_session)) -> BatchRead:
    batch = run_service.get_batch(session, batch_id)
    return BatchRead.model_validate(batch)
