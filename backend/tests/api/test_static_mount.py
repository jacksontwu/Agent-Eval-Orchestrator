import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def spa_client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>aeo</title>", encoding="utf-8")
    monkeypatch.setenv("AEO_FRONTEND_DIST", str(dist))
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    from app.core.config import get_settings
    from app.main import create_app
    get_settings.cache_clear()
    return TestClient(create_app())


def test_index_served(spa_client):
    resp = spa_client.get("/")
    assert resp.status_code == 200
    assert "aeo" in resp.text


def test_spa_fallback(spa_client):
    resp = spa_client.get("/tasks/run-123")
    assert resp.status_code == 200
    assert "<!doctype html>" in resp.text


def test_api_still_json(spa_client):
    resp = spa_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
