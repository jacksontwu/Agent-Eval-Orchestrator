from __future__ import annotations

from pathlib import Path

from app.service.errors import NotFoundError, ServiceError

_MAX_CHARS = 200_000


def read_file(path: str, allowed_roots: list[Path]) -> str:
    raw = (path or "").strip()
    if not raw:
        raise ServiceError("path is required")
    target = Path(raw).expanduser().resolve()
    roots = [root.expanduser().resolve() for root in allowed_roots]
    if not any(_is_within(target, root) for root in roots):
        raise ServiceError("path is outside readable root")
    if not target.is_file():
        raise NotFoundError("file not found")
    text = target.read_text(encoding="utf-8", errors="replace")
    return text[-_MAX_CHARS:]


def _is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False
