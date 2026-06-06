from sqlalchemy import text

from app.model.db import make_engine, make_session_factory


def test_engine_uses_wal(tmp_path):
    url = f"sqlite:///{tmp_path}/x.db"
    engine = make_engine(url)
    Session = make_session_factory(engine)
    with Session() as s:
        mode = s.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"
