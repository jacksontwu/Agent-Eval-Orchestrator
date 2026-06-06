from fastapi import APIRouter, Depends

from app.api.deps import require_token
from app.api.routes import (
    batches,
    case_runs,
    dashboard,
    datasets,
    files,
    health,
    runs,
    templates,
    worker_protocol,
    workers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])

# Authenticated sub-routers are registered in app.main with a shared token dependency.
authed_router = APIRouter(dependencies=[Depends(require_token)])
authed_router.include_router(templates.router, tags=["templates"])
authed_router.include_router(workers.router, tags=["workers"])
authed_router.include_router(datasets.router, tags=["datasets"])
authed_router.include_router(dashboard.router, tags=["dashboard"])
authed_router.include_router(runs.router, tags=["runs"])
authed_router.include_router(case_runs.router, tags=["case-runs"])
authed_router.include_router(batches.router, tags=["batches"])
authed_router.include_router(worker_protocol.router, tags=["worker-protocol"])
authed_router.include_router(files.router, tags=["files"])
