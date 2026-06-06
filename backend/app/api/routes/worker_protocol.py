from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schema.assets import AssetManifest
from app.schema.worker_protocol import (
    ClaimRequest,
    ClaimResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.service import asset_service, worker_protocol_service

router = APIRouter()


@router.post("/workers/register", response_model=RegisterResponse)
def register(body: RegisterRequest, session: Session = Depends(db_session)) -> RegisterResponse:
    worker = worker_protocol_service.register(session, body)
    return RegisterResponse(worker_id=worker.worker_id)


@router.post("/workers/heartbeat", response_model=HeartbeatResponse)
def heartbeat(body: HeartbeatRequest, session: Session = Depends(db_session)) -> HeartbeatResponse:
    worker_protocol_service.heartbeat(session, body)
    return HeartbeatResponse()


@router.post("/workers/claim", response_model=ClaimResponse)
def claim(body: ClaimRequest, session: Session = Depends(db_session)) -> ClaimResponse:
    return worker_protocol_service.claim(session, body)


@router.get("/workers/assets/{asset_manifest_id}", response_model=AssetManifest)
def get_asset_manifest(asset_manifest_id: str, session: Session = Depends(db_session)) -> AssetManifest:
    return asset_service.manifest_for(session, asset_manifest_id)


@router.get("/workers/assets/{asset_manifest_id}/file")
def get_asset_file(asset_manifest_id: str, path: str = Query(...),
                   session: Session = Depends(db_session)) -> FileResponse:
    resolved = asset_service.open_entry(session, asset_manifest_id, path)
    return FileResponse(resolved)
