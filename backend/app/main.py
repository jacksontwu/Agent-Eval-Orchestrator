from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router, authed_router
from app.service.errors import ServiceError


def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})


def create_app() -> FastAPI:
    app = FastAPI(title="agent-eval-orchestrator")
    app.include_router(api_router)
    app.include_router(authed_router, prefix="/api")
    app.add_exception_handler(ServiceError, _service_error_handler)
    return app


app = create_app()
