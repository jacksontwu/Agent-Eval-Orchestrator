import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.model.base import Base
import app.model.tables  # noqa: F401  (register models)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Prefer an explicit env override; otherwise fall back to the app's derived
    # default (sqlite under shared_root) so bare `alembic upgrade head` works.
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is required for alembic")
    # Ensure the parent directory exists for sqlite file targets.
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
