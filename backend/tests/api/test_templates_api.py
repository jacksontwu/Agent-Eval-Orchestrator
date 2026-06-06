import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import db_session


@pytest.fixture
def client(session, monkeypatch):
    # Default-deny auth: tests run in explicit dev-open mode unless they set AEO_TOKEN.
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def test_create_and_list_template(client):
    payload = {"name": "t1", "datasetRef": "terminal-bench/terminal-bench-2"}
    resp = client.post("/api/task-templates", json=payload)
    assert resp.status_code == 201, resp.text
    tid = resp.json()["templateId"]
    assert tid.startswith("tpl-")
    listed = client.get("/api/task-templates").json()["templates"]
    assert any(t["templateId"] == tid for t in listed)


def test_requires_token(monkeypatch, session):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.delenv("AEO_ALLOW_NO_AUTH", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    c = TestClient(app)
    assert c.get("/api/task-templates").status_code == 401
    assert c.get("/api/task-templates", headers={"X-AEO-Token": "secret"}).status_code == 200
    get_settings.cache_clear()
