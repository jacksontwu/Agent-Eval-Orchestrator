from agent_eval_orchestrator.controller.server import resolve_global_harbor_viewer_paths


def test_resolve_global_harbor_viewer_paths_from_jobs_dir() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths("/home/djn/code/harbor/jobs")
    assert harbor_repo == jobs_path.parent
    assert jobs_path.name == "jobs"


def test_resolve_global_harbor_viewer_paths_uses_default_when_missing() -> None:
    harbor_repo, jobs_path = resolve_global_harbor_viewer_paths(None)
    assert jobs_path.name == "jobs"
    assert harbor_repo == jobs_path.parent
