from __future__ import annotations

import io
import tarfile
from pathlib import Path

# backend/app/service/enroll_service.py -> parents[3] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = _REPO_ROOT / "scripts" / "enroll.sh.tmpl"

_BUNDLE_EXCLUDE = {".git", "runtime", "datasets", "node_modules", "dist", "build", ".venv", "__pycache__"}


def render_enroll_script(*, controller_url: str, bot_username: str, bot_password: str, worker_id: str) -> str:
    template = _TEMPLATE.read_text(encoding="utf-8")
    return (
        template
        .replace("{{CONTROLLER_URL}}", controller_url)
        .replace("{{BOT_USERNAME}}", bot_username)
        .replace("{{BOT_PASSWORD}}", bot_password)
        .replace("{{WORKER_ID}}", worker_id)
    )


def build_code_bundle(repo_roots: list[Path]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root in repo_roots:
            root = Path(root)
            if not root.is_dir():
                continue
            tar.add(root, arcname=root.name, filter=_bundle_filter)
    return buf.getvalue()


def _bundle_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(Path(info.name).parts)
    if parts & _BUNDLE_EXCLUDE:
        return None
    return info
