from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.core.config import get_settings
from app.core.ids import new_id
from app.service import enroll_service

router = APIRouter()


def _controller_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/workers/enroll.sh")
def enroll_script(request: Request, worker_id: str | None = Query(default=None, alias="workerId")) -> PlainTextResponse:
    settings = get_settings()
    script = enroll_service.render_enroll_script(
        controller_url=_controller_url(request),
        bot_username=settings.bot_username or "",
        bot_password=settings.bot_password or "",
        worker_id=worker_id or new_id("worker"),
    )
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/workers/code-bundle")
def code_bundle() -> Response:
    settings = get_settings()
    repo_root = Path(__file__).resolve().parents[4]
    roots = [repo_root / "backend", repo_root / "scripts"]
    harbor_repo = Path(settings.harbor_repo)
    if harbor_repo.is_dir():
        roots.append(harbor_repo)
    data = enroll_service.build_code_bundle(roots)
    return Response(content=data, media_type="application/gzip")
