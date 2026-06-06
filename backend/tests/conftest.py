import pytest
from sqlalchemy import event

from app.model.base import Base
import app.model.tables  # noqa: F401
from app.model.db import make_engine, make_session_factory


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
