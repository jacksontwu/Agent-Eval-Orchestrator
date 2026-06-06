from pathlib import Path

from app.core.config import Settings


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    s = Settings()
    assert s.shared_root == tmp_path
    assert s.database_url == f"sqlite:///{tmp_path}/controller/aeo.db"
    assert s.token is None


def test_explicit_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/x.db")
    assert Settings().database_url == "sqlite:////tmp/x.db"
