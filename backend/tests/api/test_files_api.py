import pytest
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.main import create_app


@pytest.fixture
def files_client(session, tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    monkeypatch.setenv("AEO_SHARED_ROOT", str(tmp_path))
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app), tmp_path


def test_read_file(files_client):
    client, root = files_client
    target = root / "archives" / "note.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello-content", encoding="utf-8")
    resp = client.get("/api/files/read", params={"path": str(target)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "hello-content"


def test_read_file_traversal_rejected(files_client):
    client, root = files_client
    resp = client.get("/api/files/read", params={"path": "/etc/passwd"})
    assert resp.status_code == 400
