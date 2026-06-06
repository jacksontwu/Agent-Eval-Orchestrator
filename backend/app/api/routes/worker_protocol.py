from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.worker_protocol import (
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.service import worker_protocol_service

router = APIRouter()


@router.post("/workers/register", response_model=RegisterResponse)
def register(body: RegisterRequest, session: Session = Depends(db_session)) -> RegisterResponse:
    worker = worker_protocol_service.register(session, body)
    return RegisterResponse(worker_id=worker.worker_id)


@router.post("/workers/heartbeat", response_model=HeartbeatResponse)
def heartbeat(body: HeartbeatRequest, session: Session = Depends(db_session)) -> HeartbeatResponse:
    worker_protocol_service.heartbeat(session, body)
    return HeartbeatResponse()
