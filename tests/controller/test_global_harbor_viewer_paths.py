from pathlib import Path
from types import SimpleNamespace

from agent_eval_orchestrator.controller import server
from agent_eval_orchestrator.controller.server import Handler
from agent_eval_orchestrator.controller.server import resolve_global_harbor_viewer_paths


def test_resolve_global_harbor_viewer_paths_from_jobs_dir() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths("/home/djn/code/harbor/jobs")
    assert harbor_repo == jobs_path.parent
    assert jobs_path.name == "jobs"


def test_resolve_global_harbor_viewer_paths_uses_default_when_missing() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths(None)
    assert jobs_path.name == "jobs"
    assert harbor_repo == jobs_path.parent


def test_global_harbor_viewer_uses_proxy_session_for_requested_jobs_dir(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()

    class HealthyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeViewerManager:
        def ensure_viewer(self, *, viewer_id: str, jobs_dir: Path):
            self.viewer_id = viewer_id
            self.jobs_dir = jobs_dir
            return SimpleNamespace(viewer_id=viewer_id, port=18100)

    manager = FakeViewerManager()
    monkeypatch.setattr(server.request, "urlopen", lambda *args, **kwargs: HealthyResponse())
    handler = SimpleNamespace(
        headers={"Host": "example.test:7380"},
        viewer_manager=manager,
        _viewer_public_url=lambda: "http://example.test:7369/",
        _rebuild_merged_jobs=lambda jobs_dir: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir))

    assert result["available"] is True
    assert result["url"].startswith("/harbor-viewer/global-")
    assert result["embeddedUrl"] == result["url"]
    assert result["jobsDir"] == str(jobs_dir.resolve())
    assert manager.jobs_dir == jobs_dir.resolve()
