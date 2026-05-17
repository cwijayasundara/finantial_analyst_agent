"""Tests for cypher_read_only and cypher_explain against Neo4j."""
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
def seeded_neo4j():
    """Spin up Neo4j, seed a tiny graph, yield (url, password)."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', name: 'Costco'})")
            s.run("CREATE (m:Merchant {id: 'merchant::tesco', name: 'Tesco'})")
            s.run(
                "MATCH (a:Merchant {id: 'merchant::costco'}), "
                "(b:Merchant {id: 'merchant::tesco'}) "
                "CREATE (a)-[:RELATED]->(b)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_cypher_read_only_returns_rows(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only
    from cookbooks._shared.neo4j_client import close_driver

    rows = cypher_read_only.invoke({
        "query": "MATCH (m:Merchant) RETURN m.id AS id, m.name AS name ORDER BY id"
    })
    close_driver()
    assert isinstance(rows, list)
    ids = {r["id"] for r in rows}
    assert ids == {"merchant::costco", "merchant::tesco"}


def test_cypher_read_only_rejects_writes(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        cypher_read_only.invoke({"query": "CREATE (n:Junk) RETURN n"})


def test_cypher_read_only_appends_implicit_limit(seeded_neo4j, monkeypatch, tmp_workspace):
    """Even a query like MATCH (m:Merchant) RETURN m must be capped."""
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only, CYPHER_DEFAULT_LIMIT
    from cookbooks._shared.neo4j_client import close_driver

    rows = cypher_read_only.invoke({"query": "MATCH (m:Merchant) RETURN m"})
    close_driver()
    assert len(rows) <= CYPHER_DEFAULT_LIMIT


def test_cypher_explain_returns_plan_without_executing(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_explain
    from cookbooks._shared.neo4j_client import close_driver

    plan = cypher_explain.invoke({"query": "MATCH (m:Merchant) RETURN m"})
    close_driver()
    assert isinstance(plan, dict)
    assert "operator_type" in plan


def test_cypher_explain_rejects_writes(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_explain
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        cypher_explain.invoke({"query": "CREATE (n:Junk) RETURN n"})
