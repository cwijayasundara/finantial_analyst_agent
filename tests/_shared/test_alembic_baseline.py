"""Verify the baseline migration creates the expected schema in Postgres."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import psycopg
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


EXPECTED_TABLES = {
    "accounts", "statements", "categories", "merchants", "patterns",
    "transactions", "annotations", "memos", "budgets", "goals",
    "net_worth_snapshots",
    "alembic_version",
}

EXPECTED_TXN_INDEXES = {
    "transactions_pkey",
    "idx_txn_date", "idx_txn_merchant", "idx_txn_category",
    "idx_txn_account_date",
    "transactions_account_id_date_amount_raw_description_key",
}


@pytest.fixture(scope="module")
def postgres_url():
    """Yields a tuple (alembic_url, psycopg_url).

    alembic_url uses the postgresql+psycopg:// scheme so SQLAlchemy picks
    the psycopg (v3) driver.  psycopg_url uses the plain postgresql:// scheme
    accepted by psycopg.connect().
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        raw = pg.get_connection_url()  # postgresql+psycopg2://...
        alembic_url = raw.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        psycopg_url = raw.replace("postgresql+psycopg2://", "postgresql://")
        yield alembic_url, psycopg_url


def test_baseline_upgrade_creates_all_tables(postgres_url, monkeypatch):
    alembic_url, psycopg_url = postgres_url
    monkeypatch.setenv("PFH_PG_URL", alembic_url)
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**os.environ, "PFH_PG_URL": alembic_url},
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr}"

    with psycopg.connect(psycopg_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        tables = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"missing tables: {EXPECTED_TABLES - tables}"
    )


def test_baseline_downgrade_drops_all_user_tables(postgres_url, monkeypatch):
    alembic_url, psycopg_url = postgres_url
    monkeypatch.setenv("PFH_PG_URL", alembic_url)
    env = {**os.environ, "PFH_PG_URL": alembic_url}
    subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "downgrade", "base"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"downgrade failed: {result.stderr}"
    with psycopg.connect(psycopg_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
        )
        tables = {r[0] for r in cur.fetchall()}
    assert tables == set(), f"orphan tables after downgrade: {tables}"


def test_baseline_creates_expected_txn_indexes(postgres_url, monkeypatch):
    alembic_url, psycopg_url = postgres_url
    monkeypatch.setenv("PFH_PG_URL", alembic_url)
    env = {**os.environ, "PFH_PG_URL": alembic_url}
    subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True,
    )
    with psycopg.connect(psycopg_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'transactions'"
        )
        indexes = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TXN_INDEXES.issubset(indexes), (
        f"missing indexes: {EXPECTED_TXN_INDEXES - indexes}"
    )
