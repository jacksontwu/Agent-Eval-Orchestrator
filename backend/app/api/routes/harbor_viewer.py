from fastapi import APIRouter

from app.service.orchestration import viewer_manager

router = APIRouter()


@router.get("/harbor-viewer/global")
def harbor_viewer_global() -> dict:
    return viewer_manager.ensure_global()
