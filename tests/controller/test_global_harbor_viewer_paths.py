from types import SimpleNamespace

from agent_eval_orchestrator.controller import server
from agent_eval_orchestrator.controller.server import Handler
from agent_eval_orchestrator.controller.server import resolve_controller_viewer_harbor_repo
from agent_eval_orchestrator.controller.server import resolve_global_harbor_viewer_paths


def test_resolve_global_harbor_viewer_paths_from_jobs_dir() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths("/home/djn/code/harbor/jobs")
    assert harbor_repo == jobs_path.parent
    assert jobs_path.name == "jobs"


def test_resolve_global_harbor_viewer_paths_uses_default_when_missing() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths(None)
    assert jobs_path.name == "jobs"
    assert harbor_repo == jobs_path.parent


def test_resolve_controller_viewer_harbor_repo_prefers_aeo_env(tmp_path, monkeypatch) -> None:
    harbor_repo = tmp_path / "harbor"
    monkeypatch.setenv("AEO_HARBOR_REPO", str(harbor_repo))

    assert resolve_controller_viewer_harbor_repo() == harbor_repo.resolve()


def test_resolve_controller_viewer_harbor_repo_falls_back_to_harbor_probe(tmp_path, monkeypatch) -> None:
    harbor_repo = tmp_path / "detected-harbor"
    monkeypatch.delenv("AEO_HARBOR_REPO", raising=False)
    monkeypatch.setattr(server, "resolve_controller_harbor_repo", lambda: harbor_repo)

    assert resolve_controller_viewer_harbor_repo() == harbor_repo


def test_global_harbor_viewer_requires_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.delenv("AEO_HARBOR_VIEWER_URL", raising=False)

    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir))

    assert result["available"] is False
    assert "AEO_HARBOR_VIEWER_URL" in result["reason"]
    assert result["jobsDir"] == str(jobs_dir.resolve())


def test_global_harbor_viewer_returns_configured_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("AEO_HARBOR_VIEWER_URL", "http://viewer.example.test:7369/")
    seen_urls = []

    class HealthyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout):
        seen_urls.append(url)
        return HealthyResponse()

    monkeypatch.setattr(server.request, "urlopen", fake_urlopen)
    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir), run_id="run-1")

    assert result["available"] is True
    assert result["url"] == "http://viewer.example.test:7369/"
    assert result["embeddedUrl"] == "http://viewer.example.test:7369/"
    assert result["jobsDir"] == str(jobs_dir.resolve())
    assert seen_urls == ["http://viewer.example.test:7369/api/health"]


def test_global_harbor_viewer_reports_unhealthy_external_url(tmp_path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("AEO_HARBOR_VIEWER_URL", "http://127.0.0.1:65530")

    def fake_urlopen(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(server.request, "urlopen", fake_urlopen)
    handler = SimpleNamespace(
        _rebuild_merged_jobs=lambda jobs_dir, run_id=None: None,
    )

    result = Handler._ensure_global_harbor_viewer(handler, str(jobs_dir))

    assert result["available"] is False
    assert "http://127.0.0.1:65530" in result["reason"]
    assert "connection refused" in result["reason"]
