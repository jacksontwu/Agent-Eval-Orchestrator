from fastapi import APIRouter, Depends

from app.api.deps import require_token
from app.api.routes import health, templates, workers

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])

# Authenticated sub-routers are registered in app.main with a shared token dependency.
authed_router = APIRouter(dependencies=[Depends(require_token)])
authed_router.include_router(templates.router, tags=["templates"])
authed_router.include_router(workers.router, tags=["workers"])
