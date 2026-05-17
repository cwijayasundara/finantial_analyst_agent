"""Alembic environment for openclaw Postgres.

Pulls the connection URL from PFH_PG_URL (see cookbooks/_shared/config.py)
so the same migrations can target a developer instance, CI, or a test
container without editing alembic.ini.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from env at runtime.
pg_url = os.environ.get("PFH_PG_URL")
if not pg_url:
    raise RuntimeError(
        "PFH_PG_URL is not set. Export it before running alembic, e.g.\n"
        "  export PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"
    )
config.set_main_option("sqlalchemy.url", pg_url)

# We don't use SQLAlchemy ORM models — migrations are hand-authored DDL.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=pg_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
