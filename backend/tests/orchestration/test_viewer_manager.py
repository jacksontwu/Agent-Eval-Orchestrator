from app.service.orchestration.viewer_manager import HarborViewerManager


def test_pick_port_returns_first_free(tmp_path):
    mgr = HarborViewerManager(harbor_repo=tmp_path, logs_dir=tmp_path / "logs",
                              port_start=18100, port_end=18105)
    assert mgr._pick_port() == 18100


def test_logs_dir_created(tmp_path):
    logs = tmp_path / "logs"
    HarborViewerManager(harbor_repo=tmp_path, logs_dir=logs)
    assert logs.is_dir()
