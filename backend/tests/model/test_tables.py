from sqlalchemy import inspect

from app.model.base import Base
from app.model.db import make_engine
import app.model.tables  # noqa: F401  (registers models)

EXPECTED = {
    "task_templates", "runs", "batches", "case_runs",
    "workers", "asset_sync_jobs", "run_rerun_jobs",
}


def test_metadata_has_all_tables():
    assert EXPECTED.issubset(set(Base.metadata.tables))


def test_create_all(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/x.db")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert EXPECTED.issubset(names)


def test_worker_has_no_ssh_columns():
    cols = {c.name for c in Base.metadata.tables["workers"].columns}
    assert "ssh_host_alias" not in cols
    assert "connection_mode" not in cols
    assert {"worker_id", "slots_total", "slots_used", "allocation_weight"}.issubset(cols)
