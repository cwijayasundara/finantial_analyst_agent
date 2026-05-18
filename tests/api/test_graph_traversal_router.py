"""Tests for the /api/graph/{node,neighbors,evidence} endpoints."""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient
from testcontainers.neo4j import Neo4jContainer


docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_neo4j_with_path():
    """Neo4j with merchant + transaction + category — a 2-hop path."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', canonical_name: 'Costco'})")
            s.run("CREATE (c:Category {id: 'category::groceries', name: 'Groceries'})")
            s.run(
                "CREATE (t:Transaction {id: 'tx::s1::1', date: '2026-03-15', amount: 50.00})"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (m:Merchant {id: 'merchant::costco'}) "
                "CREATE (t)-[:AT_MERCHANT]->(m)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


@pytest.fixture
def client(seeded_neo4j_with_path, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_path
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks.api.server import app
    yield TestClient(app)
    from cookbooks._shared.neo4j_client import close_driver
    close_driver()


def test_get_node_returns_node(client):
    r = client.get("/api/graph/node/merchant::costco")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "merchant::costco"
    assert data["label"] == "Merchant"
    assert data["properties"]["canonical_name"] == "Costco"


def test_get_node_404_when_missing(client):
    r = client.get("/api/graph/node/merchant::nonexistent")
    assert r.status_code == 404


def test_get_neighbors_depth_one(client):
    r = client.get("/api/graph/neighbors/merchant::costco?depth=1")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    labels = {n["label"] for n in data["nodes"]}
    assert "Merchant" in labels
    assert "Transaction" in labels


def test_get_neighbors_depth_two_reaches_category(client):
    r = client.get("/api/graph/neighbors/merchant::costco?depth=2")
    assert r.status_code == 200
    data = r.json()
    labels = {n["label"] for n in data["nodes"]}
    assert "Category" in labels


def test_get_neighbors_invalid_depth_rejected(client):
    """depth > 4 is rejected by FastAPI's Query(le=4) → 422 Unprocessable Entity."""
    r = client.get("/api/graph/neighbors/merchant::costco?depth=10")
    assert r.status_code == 422
