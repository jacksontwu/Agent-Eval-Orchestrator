import subprocess
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_creates_tables(tmp_path):
    db = tmp_path / "m.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": __import__("os").environ["PATH"]}
    out = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd="."  , env=env, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    names = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"task_templates", "runs", "batches", "case_runs", "workers"}.issubset(names)
