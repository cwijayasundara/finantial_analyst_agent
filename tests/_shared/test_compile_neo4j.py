"""End-to-end: seed Postgres + Wiki → compile to Neo4j → assert counts match."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.neo4j import Neo4jContainer

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def both_containers():
    """Spin up Postgres + Neo4j; run alembic + init.cypher."""
    pg = PostgresContainer("postgres:16-alpine")
    n4 = Neo4jContainer("neo4j:5.26-community")
    pg.start()
    n4.start()
    try:
        raw_pg_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_pg_url = raw_pg_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        n4_url = n4.get_connection_url()
        n4_pw = n4.password

        # Alembic upgrade (uses SQLAlchemy URL).
        subprocess.run(
            ["uv", "run", "alembic",
             "-c", str(REPO_ROOT / "db" / "postgres" / "alembic.ini"),
             "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "PFH_PG_URL": alembic_pg_url},
            check=True, capture_output=True,
        )

        # init.cypher (uses bolt URL).
        subprocess.run(
            ["uv", "run", "python", "-m", "cookbooks._shared.init_neo4j"],
            cwd=REPO_ROOT,
            env={**os.environ,
                 "PFH_NEO4J_URL": n4_url,
                 "PFH_NEO4J_PASSWORD": n4_pw},
            check=True, capture_output=True,
        )

        yield raw_pg_url, n4_url, n4_pw
    finally:
        n4.stop()
        pg.stop()


def _set_env(monkeypatch, raw_pg_url, n4_url, n4_pw):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", raw_pg_url)
    monkeypatch.setenv("PFH_NEO4J_URL", n4_url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", n4_pw)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import importlib, sys
    for mod in ("cookbooks._shared.db",):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


def test_compile_neo4j_writes_account_node(both_containers, monkeypatch, tmp_workspace):
    raw_pg_url, n4_url, n4_pw = both_containers
    _set_env(monkeypatch, raw_pg_url, n4_url, n4_pw)

    # Seed one row.
    from cookbooks._shared.db import connect_readwrite
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, type, currency) "
            "VALUES (%s, %s, %s, %s)",
            ["acct-test", "Test Savings", "savings", "GBP"],
        )
        conn.commit()
    finally:
        conn.close()

    # Compile.
    from cookbooks._shared.compile_neo4j import compile_to_neo4j
    from cookbooks._shared.neo4j_client import close_driver
    n_nodes, n_edges = compile_to_neo4j()
    assert n_nodes >= 1
    # Verify the Account node landed.
    from cookbooks._shared.neo4j_client import session
    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (n:Account {id: $id}) RETURN n.name AS name, n.currency AS ccy",
            id="acct-test",
        ).single()
    close_driver()
    assert rec is not None
    assert rec["name"] == "Test Savings"
    assert rec["ccy"] == "GBP"


def test_compile_neo4j_is_idempotent(both_containers, monkeypatch, tmp_workspace):
    """Re-running compile must not duplicate nodes (MERGE-on-id)."""
    raw_pg_url, n4_url, n4_pw = both_containers
    _set_env(monkeypatch, raw_pg_url, n4_url, n4_pw)

    from cookbooks._shared.compile_neo4j import compile_to_neo4j
    from cookbooks._shared.neo4j_client import close_driver, session
    # Two runs back to back. The first one may early-exit via fingerprint
    # if the previous test cached the same state — call with force=True
    # to be deterministic.
    compile_to_neo4j(force=True)
    compile_to_neo4j(force=True)

    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (n:Account {id: $id}) RETURN count(n) AS c",
            id="acct-test",
        ).single()
    close_driver()
    assert rec["c"] == 1  # MERGE, not CREATE — exactly one
