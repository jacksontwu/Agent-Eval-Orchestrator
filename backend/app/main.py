from __future__ import annotations

from fastapi import FastAPI

from app.api.router import api_router, authed_router


def create_app() -> FastAPI:
    app = FastAPI(title="agent-eval-orchestrator")
    app.include_router(api_router)
    app.include_router(authed_router, prefix="/api")
    return app


app = create_app()
