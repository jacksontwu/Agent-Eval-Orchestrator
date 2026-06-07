from fastapi import APIRouter, Depends

from app.api.deps import require_current_principal
from app.api.routes import (
    auth,
    batches,
    case_runs,
    dashboard,
    datasets,
    enroll,
    files,
    harbor_viewer,
    health,
    runs,
    templates,
    worker_protocol,
    workers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])

authed_router = APIRouter(dependencies=[Depends(require_current_principal)])
authed_router.include_router(templates.router, tags=["templates"])
authed_router.include_router(workers.router, tags=["workers"])
authed_router.include_router(datasets.router, tags=["datasets"])
authed_router.include_router(dashboard.router, tags=["dashboard"])
authed_router.include_router(runs.router, tags=["runs"])
authed_router.include_router(case_runs.router, tags=["case-runs"])
authed_router.include_router(batches.router, tags=["batches"])
authed_router.include_router(worker_protocol.router, tags=["worker-protocol"])
authed_router.include_router(files.router, tags=["files"])
authed_router.include_router(harbor_viewer.router, tags=["harbor-viewer"])
authed_router.include_router(enroll.router, tags=["enroll"])
