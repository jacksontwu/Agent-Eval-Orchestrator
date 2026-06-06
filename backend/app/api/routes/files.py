from pathlib import Path

from fastapi import APIRouter, Query

from app.core.config import get_settings
from app.service import files_service

router = APIRouter()


@router.get("/files/read")
def read_file(path: str = Query(...)) -> dict:
    settings = get_settings()
    allowed = [Path(settings.shared_root), Path(settings.harbor_repo)]
    content = files_service.read_file(path, allowed)
    return {"path": str(Path(path).expanduser().resolve()), "content": content}
