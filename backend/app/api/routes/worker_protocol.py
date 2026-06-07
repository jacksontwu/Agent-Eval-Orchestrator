from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.permissions import PermissionCode
from app.schema.assets import AssetManifest
from app.schema.worker_protocol import (
    ClaimRequest,
    ClaimResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    JobArchiveResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.service import asset_service, worker_protocol_service
from app.service.orchestration import result_collector

router = APIRouter()


@router.post(
    "/workers/register",
    response_model=RegisterResponse,
    dependencies=[Depends(require_permission(PermissionCode.WORKER_PROTOCOL_USE))],
)
def register(body: RegisterRequest, session: Session = Depends(db_session)) -> RegisterResponse:
    worker = worker_protocol_service.register(session, body)
    return RegisterResponse(worker_id=worker.worker_id)


@router.post(
    "/workers/heartbeat",
    response_model=HeartbeatResponse,
    dependencies=[Depends(require_permission(PermissionCode.WORKER_PROTOCOL_USE))],
)
def heartbeat(body: HeartbeatRequest, session: Session = Depends(db_session)) -> HeartbeatResponse:
    worker_protocol_service.heartbeat(session, body)
    return HeartbeatResponse()


@router.post(
    "/workers/claim",
    response_model=ClaimResponse,
    dependencies=[Depends(require_permission(PermissionCode.WORKER_PROTOCOL_USE))],
)
def claim(body: ClaimRequest, session: Session = Depends(db_session)) -> ClaimResponse:
    return worker_protocol_service.claim(session, body)


@router.get(
    "/workers/assets/{asset_manifest_id}",
    response_model=AssetManifest,
    dependencies=[Depends(require_permission(PermissionCode.ASSETS_USE))],
)
def get_asset_manifest(asset_manifest_id: str, session: Session = Depends(db_session)) -> AssetManifest:
    return asset_service.manifest_for(session, asset_manifest_id)


@router.get(
    "/workers/assets/{asset_manifest_id}/file",
    dependencies=[Depends(require_permission(PermissionCode.ASSETS_USE))],
)
def get_asset_file(asset_manifest_id: str, path: str = Query(...),
                   session: Session = Depends(db_session)) -> FileResponse:
    resolved = asset_service.open_entry(session, asset_manifest_id, path)
    return FileResponse(resolved)


@router.post(
    "/workers/job-archive",
    response_model=JobArchiveResponse,
    dependencies=[Depends(require_permission(PermissionCode.ASSETS_USE))],
)
def job_archive(batch_id: str = Form(alias="batchId"), sha256: str = Form(...),
                archive: UploadFile = File(...),
                session: Session = Depends(db_session)) -> JobArchiveResponse:
    from app.core.config import get_settings
    from app.core.layout import default_layout

    layout = default_layout(get_settings().shared_root)
    result_collector.ingest_archive(session, batch_id=batch_id, sha256=sha256,
                                     file_stream=archive.file, layout=layout)
    return JobArchiveResponse(batch_id=batch_id)
