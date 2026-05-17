"""Tests for merchant_resolve (full-text path)."""
from __future__ import annotations

import subprocess

import pytest
from testcontainers.neo4j import Neo4jContainer


docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_neo4j_with_index():
    """Neo4j with merchant_fulltext index + a few merchants seeded."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            # Constraint + full-text index (subset of init.cypher).
            s.run(
                "CREATE CONSTRAINT merchant_id_unique IF NOT EXISTS "
                "FOR (n:Merchant) REQUIRE n.id IS UNIQUE"
            )
            s.run(
                "CREATE FULLTEXT INDEX merchant_fulltext IF NOT EXISTS "
                "FOR (n:Merchant) ON EACH [n.canonical_name, n.aliases]"
            )
            # Seed merchants — note the deliberately noisy alias.
            s.run(
                "CREATE (m:Merchant {id: 'merchant::costco', "
                "canonical_name: 'Costco', aliases: ['COSTCO WHSE', 'COSTCO.COM']})"
            )
            s.run(
                "CREATE (m:Merchant {id: 'merchant::tesco', "
                "canonical_name: 'Tesco', aliases: ['Tesco Stores', 'TSC*TESCO']})"
            )
            s.run(
                "CREATE (m:Merchant {id: 'merchant::amazon', "
                "canonical_name: 'Amazon', aliases: ['AMZN', 'AMZN MKTP']})"
            )
            # Full-text index needs a moment to populate after writes.
            s.run("CALL db.awaitIndexes(5)")
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_merchant_resolve_finds_canonical_name(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve.invoke({"query": "Costco", "k": 3})
    close_driver()
    assert len(hits) >= 1
    assert hits[0]["id"] == "merchant::costco"
    assert hits[0]["canonical_name"] == "Costco"
    assert hits[0]["score"] > 0


def test_merchant_resolve_finds_via_alias(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve.invoke({"query": "AMZN MKTP", "k": 3})
    close_driver()
    assert len(hits) >= 1
    assert hits[0]["id"] == "merchant::amazon"


def test_merchant_resolve_returns_empty_for_unknown(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve.invoke({"query": "NoSuchMerchantEver", "k": 3})
    close_driver()
    assert hits == []


def test_merchant_resolve_caps_at_k(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve.invoke({
        "query": "merchant OR Costco OR Tesco OR Amazon", "k": 1
    })
    close_driver()
    assert len(hits) == 1
