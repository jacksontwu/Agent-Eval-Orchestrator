from app.core.layout import Layout, default_layout


def test_layout_dirs(tmp_path):
    layout = Layout(root=tmp_path)
    assert layout.controller_dir == tmp_path / "controller"
    assert layout.imported_jobs_dir == tmp_path / "controller" / "imported-jobs"
    layout.ensure_dirs()
    assert layout.controller_dir.is_dir()


def test_default_layout(tmp_path):
    assert default_layout(tmp_path).root == tmp_path.resolve()
