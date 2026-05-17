"""Tests for sql_read_only against Postgres."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )

        import psycopg
        with psycopg.connect(raw_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (id, name, type, currency) "
                "VALUES ('acct1', 'Test', 'savings', 'GBP')"
            )
        yield raw_url


def _wire_env(monkeypatch, raw_url):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", raw_url)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_sql_read_only_returns_rows(seeded_postgres, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only

    rows = sql_read_only.invoke({
        "query": "SELECT id, name FROM accounts WHERE id = %s",
        "params": ["acct1"],
    })
    assert rows == [{"id": "acct1", "name": "Test"}]


def test_sql_read_only_rejects_writes(seeded_postgres, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        sql_read_only.invoke({
            "query": "INSERT INTO accounts (id, name, type) VALUES ('x', 'y', 'z')"
        })


def test_sql_read_only_rejects_write_via_postgres_transaction(seeded_postgres, monkeypatch, tmp_workspace):
    """Even if the keyword guard had a hole, SET TRANSACTION READ ONLY
    is the second-line defense and the DB itself rejects writes."""
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import _connect_readonly
    import psycopg

    conn = _connect_readonly()
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES ('x', 'y', 'z')",
            )
    finally:
        conn.close()


def test_sql_read_only_appends_implicit_limit(seeded_postgres, monkeypatch, tmp_workspace):
    """SELECT without LIMIT gets capped at SQL_DEFAULT_LIMIT."""
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only, SQL_DEFAULT_LIMIT

    rows = sql_read_only.invoke({"query": "SELECT id FROM accounts"})
    assert len(rows) <= SQL_DEFAULT_LIMIT
