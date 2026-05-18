"""Tests for evidence_for and neighbors graph-traversal tools."""
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
def graph_with_evidence():
    """Neo4j with one merchant + 3 transactions + 2 categories."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', canonical_name: 'Costco'})")
            s.run("CREATE (c:Category {id: 'category::groceries', name: 'Groceries'})")
            s.run("CREATE (c:Category {id: 'category::household', name: 'Household'})")
            for tx in ("tx::s1::1", "tx::s1::2", "tx::s1::3"):
                s.run(
                    "CREATE (t:Transaction {id: $id, date: '2026-03-15', amount: 50.00})",
                    {"id": tx},
                )
                s.run(
                    "MATCH (t:Transaction {id: $id}), (m:Merchant {id: 'merchant::costco'}) "
                    "CREATE (t)-[:AT_MERCHANT]->(m)",
                    {"id": tx},
                )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::2'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::3'}), (c:Category {id: 'category::household'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_evidence_for_returns_transactions(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import evidence_for
    from cookbooks._shared.neo4j_client import close_driver

    evidence = evidence_for.invoke({"node_id": "merchant::costco", "k": 10})
    close_driver()
    ids = {e["id"] for e in evidence}
    assert {"tx::s1::1", "tx::s1::2", "tx::s1::3"}.issubset(ids)


def test_evidence_for_respects_k(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import evidence_for
    from cookbooks._shared.neo4j_client import close_driver

    evidence = evidence_for.invoke({"node_id": "merchant::costco", "k": 2})
    close_driver()
    assert len(evidence) == 2


def test_neighbors_depth_one(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import neighbors
    from cookbooks._shared.neo4j_client import close_driver

    subgraph = neighbors.invoke({"node_id": "merchant::costco", "depth": 1})
    close_driver()
    labels = {n["label"] for n in subgraph["nodes"]}
    assert "Transaction" in labels
    assert "Merchant" in labels


def test_neighbors_depth_two_reaches_categories(graph_with_evidence, monkeypatch, tmp_workspace):
    """depth=2 should pull in categories (via transactions)."""
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import neighbors
    from cookbooks._shared.neo4j_client import close_driver

    subgraph = neighbors.invoke({"node_id": "merchant::costco", "depth": 2})
    close_driver()
    labels = {n["label"] for n in subgraph["nodes"]}
    assert "Category" in labels
