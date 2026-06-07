from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router, authed_router
from app.model.db import get_session
from app.service.errors import ServiceError
from app.service.orchestration import reaper, scheduler
from app.service.orchestration.manager import OrchestrationManager


def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})


@asynccontextmanager
async def lifespan(app: FastAPI):
    mgr: OrchestrationManager | None = None
    with get_session() as session:
        from app.model import repo_auth

        repo_auth.bootstrap_rbac(session)
    if not os.environ.get("AEO_DISABLE_ORCHESTRATION"):
        mgr = OrchestrationManager([
            lambda stop: scheduler.run_loop(stop, get_session, interval=5),
            lambda stop: reaper.run_loop(stop, get_session, interval=5),
        ])
        mgr.start()
    try:
        yield
    finally:
        if mgr is not None:
            mgr.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="agent-eval-orchestrator", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(authed_router, prefix="/api")
    app.add_exception_handler(ServiceError, _service_error_handler)
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    from app.core.config import get_settings

    dist = Path(get_settings().frontend_dist)
    if not dist.is_dir():
        return
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(dist / "index.html")


app = create_app()
