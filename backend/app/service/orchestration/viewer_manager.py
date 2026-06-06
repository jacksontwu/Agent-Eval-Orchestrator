from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from urllib import request

from app.core.config import get_settings
from app.service.normalizers.harbor_timestamps import normalize_jobs_dir


@dataclass
class ViewerSession:
    viewer_id: str
    port: int
    jobs_dir: Path
    process: subprocess.Popen


class HarborViewerManager:
    def __init__(self, *, harbor_repo: Path, logs_dir: Path, port_start: int = 18100, port_end: int = 18150) -> None:
        self.harbor_repo = harbor_repo
        self.logs_dir = logs_dir
        self.port_start = port_start
        self.port_end = port_end
        self.sessions: dict[str, ViewerSession] = {}
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _pick_port(self) -> int:
        used = {session.port for session in self.sessions.values() if session.process.poll() is None}
        for port in range(self.port_start, self.port_end + 1):
            if port not in used:
                return port
        raise RuntimeError("no available harbor viewer port")

    def _wait_ready(self, port: int, timeout_sec: int = 15) -> None:
        deadline = time.time() + timeout_sec
        url = f"http://127.0.0.1:{port}/api/health"
        while time.time() < deadline:
            try:
                with request.urlopen(url, timeout=1):
                    return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("harbor viewer did not become ready in time")

    def ensure_viewer(self, *, viewer_id: str, jobs_dir: Path) -> ViewerSession:
        existing = self.sessions.get(viewer_id)
        if existing and existing.process.poll() is None:
            return existing
        if not jobs_dir.exists():
            raise RuntimeError(f"jobs dir not found: {jobs_dir}")
        normalize_jobs_dir(jobs_dir)
        port = self._pick_port()
        log_path = self.logs_dir / f"harbor-viewer-{viewer_id}.log"
        command = (
            f"cd {self.harbor_repo} && "
            f"uv run harbor view {jobs_dir} --host 127.0.0.1 --port {port} --no-build"
        )
        log_handle = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        session = ViewerSession(
            viewer_id=viewer_id,
            port=port,
            jobs_dir=jobs_dir,
            process=process,
        )
        self.sessions[viewer_id] = session
        self._wait_ready(port)
        return session


_global_manager: HarborViewerManager | None = None


def _session_dto(session: ViewerSession) -> dict:
    return {
        "viewerId": session.viewer_id,
        "port": session.port,
        "url": f"http://127.0.0.1:{session.port}",
        "jobsDir": str(session.jobs_dir),
    }


def ensure_global() -> dict:
    """Ensure a Harbor viewer is running over the controller's global jobs dir."""
    global _global_manager
    settings = get_settings()
    harbor_repo = Path(settings.harbor_repo)
    jobs_dir = harbor_repo / "jobs"
    if _global_manager is None:
        logs_dir = Path(settings.shared_root) / "controller" / "logs"
        _global_manager = HarborViewerManager(harbor_repo=harbor_repo, logs_dir=logs_dir)
    session = _global_manager.ensure_viewer(viewer_id="global", jobs_dir=jobs_dir)
    return _session_dto(session)
