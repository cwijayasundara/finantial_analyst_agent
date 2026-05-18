"""Smoke tests for the MCP server module."""
from __future__ import annotations

import subprocess

import pytest


def test_mcp_server_module_imports():
    """The module must import cleanly so the stdio entry point works."""
    from cookbooks.api import mcp_server
    assert hasattr(mcp_server, "server")


def test_mcp_server_registers_expected_tools():
    """Five tools must be wired into the server."""
    from cookbooks.api import mcp_server
    tool_names = set(mcp_server.TOOL_NAMES)
    expected = {
        "cypher_read_only", "sql_read_only", "merchant_resolve",
        "evidence_for", "neighbors",
    }
    assert tool_names == expected


docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)


@docker_required
def test_mcp_server_exposes_runnable_cypher_tool(monkeypatch, tmp_workspace):
    """Boot the server's cypher_read_only against a testcontainers Neo4j."""
    from testcontainers.neo4j import Neo4jContainer
    with Neo4jContainer("neo4j:5.26-community") as n4:
        monkeypatch.setenv("PFH_NEO4J_URL", n4.get_connection_url())
        monkeypatch.setenv("PFH_NEO4J_PASSWORD", n4.password)
        from cookbooks._shared.config import load_settings
        if hasattr(load_settings, "cache_clear"):
            load_settings.cache_clear()
        from cookbooks.api import mcp_server
        from cookbooks._shared.neo4j_client import close_driver

        # mcp_server.cypher_read_only is wrapped — call the underlying tool directly.
        from cookbooks._shared.tools.cypher_tools import cypher_read_only as _cypher
        rows = _cypher.invoke({"query": "RETURN 1 AS x"})
        close_driver()
        assert rows == [{"x": 1}]
