from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
    return app


app = create_app()
