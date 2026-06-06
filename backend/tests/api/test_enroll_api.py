import pytest
from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.main import create_app


@pytest.fixture
def token_client(session, monkeypatch):
    monkeypatch.setenv("AEO_TOKEN", "secret")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    monkeypatch.delenv("AEO_ALLOW_NO_AUTH", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    yield TestClient(app)
    get_settings.cache_clear()


def test_enroll_script(token_client):
    resp = token_client.get("/api/workers/enroll.sh", params={"token": "secret"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/x-shellscript")
    body = resp.text
    assert "secret" in body
    assert "uv sync" in body
    assert "register" in body
    assert "/api/workers/code-bundle" in body


def test_enroll_requires_token(token_client):
    assert token_client.get("/api/workers/enroll.sh").status_code == 401
