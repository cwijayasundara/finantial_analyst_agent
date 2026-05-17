"""Tests for the Postgres ledger backend."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def fresh_postgres():
    """Spin up Postgres + run alembic to head. Yields the raw psycopg URL."""
    with PostgresContainer("postgres:16-alpine") as pg:
        # Use the same URL shape Bundle 2's test discovered:
        # alembic wants postgresql+psycopg://; psycopg.connect wants postgresql://.
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace("postgresql://", "postgresql+psycopg://")
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        yield raw_url


def test_connect_readwrite_can_insert_and_select(fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readwrite

    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, type) VALUES (%s, %s, %s)",
            ["acct1", "Test Savings", "savings"],
        )
        conn.commit()
        rows = conn.execute("SELECT id, name FROM accounts").fetchall()
        assert rows == [("acct1", "Test Savings")]
    finally:
        conn.close()


def test_connect_readonly_rejects_writes(fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readonly
    import psycopg

    conn = connect_readonly()
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES (%s, %s, %s)",
                ["acct2", "X", "savings"],
            )
    finally:
        conn.close()


def test_execute_returns_dictlike_results(fresh_postgres, monkeypatch):
    """Callers use conn.execute(sql).fetchall() returning tuple rows."""
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readwrite

    conn = connect_readwrite()
    try:
        result = conn.execute("SELECT 1 AS x, 'hi' AS y")
        rows = result.fetchall()
        assert rows == [(1, "hi")]
        single = conn.execute("SELECT count(*) FROM accounts").fetchone()
        assert isinstance(single, tuple)
        assert single[0] >= 0
    finally:
        conn.close()


def test_init_schema_is_noop_when_alembic_already_applied(fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import init_schema
    init_schema()  # must not raise
