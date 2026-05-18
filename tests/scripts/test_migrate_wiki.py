"""Tests for the one-time wiki -> Postgres migration script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture
def staged_wiki(tmp_path):
    """Build a tiny wiki tree on disk; return its path."""
    wiki = tmp_path / "wiki"
    (wiki / "merchants").mkdir(parents=True)
    (wiki / "accounts").mkdir(parents=True)
    (wiki / "categories").mkdir(parents=True)
    (wiki / "accounts" / "acct_savings.md").write_text(
        "---\n"
        "id: acct_savings\n"
        "name: Savings\n"
        "type: savings\n"
        "currency: GBP\n"
        "holder: Test User\n"
        "---\n"
        "Notes.\n"
    )
    (wiki / "categories" / "cat_groceries.md").write_text(
        "---\n"
        "id: 1\n"
        "name: groceries\n"
        "parent_id: null\n"
        "---\n"
        "Body.\n"
    )
    (wiki / "merchants" / "merchant_costco.md").write_text(
        "---\n"
        "id: merchant_costco\n"
        "canonical_name: Costco\n"
        "category_id: 1\n"
        "aliases: [COSTCO WHSE, COSTCO.COM]\n"
        "---\n"
        "Body.\n"
    )
    return wiki


@pytest.fixture
def fresh_postgres():
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
        yield raw_url


def test_migrate_inserts_accounts_categories_merchants(staged_wiki, fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    counts = migrate(dry_run=False)
    assert counts["accounts"] == 1
    assert counts["categories"] == 1
    assert counts["merchants"] == 1

    with psycopg.connect(fresh_postgres) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM accounts")
        assert cur.fetchall() == [("acct_savings", "Savings")]
        cur.execute("SELECT id, name FROM categories")
        assert cur.fetchall() == [(1, "groceries")]
        cur.execute("SELECT id, canonical_name FROM merchants")
        assert cur.fetchall() == [("merchant_costco", "Costco")]


def test_migrate_is_idempotent(staged_wiki, fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    migrate(dry_run=False)
    counts2 = migrate(dry_run=False)
    assert counts2["merchants"] == 1


def test_migrate_dry_run_does_not_write(staged_wiki, fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    counts = migrate(dry_run=True)
    assert counts["merchants"] == 1
    with psycopg.connect(fresh_postgres) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM merchants")
        assert cur.fetchone()[0] == 0
