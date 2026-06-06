import pytest
from sqlalchemy import event

from app.core.config import Settings
from app.model.base import Base
import app.model.tables  # noqa: F401
from app.model.db import make_engine, make_session_factory


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Tests must not pick up a developer's on-disk .env; force env-vars only."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = make_session_factory(engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(session, monkeypatch):
    """FastAPI TestClient in dev-open auth mode, sharing the temp-db session."""
    from fastapi.testclient import TestClient

    from app.api.deps import db_session
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AEO_ALLOW_NO_AUTH", "1")
    monkeypatch.setenv("AEO_DISABLE_ORCHESTRATION", "1")
    monkeypatch.delenv("AEO_TOKEN", raising=False)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: session
    return TestClient(app)
