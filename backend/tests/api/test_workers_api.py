import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import db_session
from app.model import repo_workers


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def _seed(session):
    repo_workers.upsert_worker(session, worker_id="w1", display_name="W1", host="h", slots_total=2, capabilities={})
    repo_workers.upsert_worker(session, worker_id="w2", display_name="W2", host="h", slots_total=1, capabilities={})
    session.commit()


def test_list_workers(client, session):
    _seed(session)
    workers = client.get("/api/workers").json()["workers"]
    assert {w["workerId"] for w in workers} == {"w1", "w2"}


def test_update_settings_flips_enabled(client, session):
    _seed(session)
    resp = client.post("/api/workers/w1/settings", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
    assert repo_workers.get_worker(session, "w1").enabled == 0


def test_delete_worker(client, session):
    _seed(session)
    resp = client.delete("/api/workers/w2")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert repo_workers.get_worker(session, "w2") is None


def test_update_missing_worker_404(client, session):
    resp = client.post("/api/workers/nope/settings", json={"enabled": False})
    assert resp.status_code == 404
