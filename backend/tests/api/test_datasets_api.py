import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import db_session


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)


def test_datasets_availability(client, monkeypatch, tmp_path):
    present = tmp_path / "present"
    present.mkdir()
    missing = tmp_path / "missing"
    import app.core.defaults as defaults
    monkeypatch.setattr(defaults, "DEFAULT_PRESET_DATASETS", {
        "ds/present": present,
        "ds/missing": missing,
    })
    data = client.get("/api/datasets").json()["datasets"]
    by_ref = {d["datasetRef"]: d for d in data}
    assert by_ref["ds/present"]["available"] is True
    assert by_ref["ds/missing"]["available"] is False
