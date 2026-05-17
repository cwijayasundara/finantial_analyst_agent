"""Tests for the thin neo4j driver wrapper."""
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
def neo4j_url():
    with Neo4jContainer("neo4j:5.26-community") as n:
        yield n.get_connection_url(), n.password


def test_driver_singleton_is_reused(neo4j_url, monkeypatch):
    url, password = neo4j_url
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared import neo4j_client
    d1 = neo4j_client.driver()
    d2 = neo4j_client.driver()
    assert d1 is d2
    neo4j_client.close_driver()


def test_session_runs_a_query(neo4j_url, monkeypatch):
    url, password = neo4j_url
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.neo4j_client import session, close_driver

    with session() as s:
        result = s.run("RETURN 1 AS x").single()
        assert result["x"] == 1
    close_driver()
