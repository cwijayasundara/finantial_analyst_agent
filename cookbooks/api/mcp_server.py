"""openclaw MCP server — stdio transport.

Exposes five general-purpose verbs to Claude Code (or any MCP client):
  - cypher_read_only(query, params)
  - sql_read_only(query, params)
  - merchant_resolve(query, k)
  - evidence_for(node_id, k)
  - neighbors(node_id, depth)

None of these are question-specific — the client (Claude Code) composes
them. Same redactor, same audit log: every remote LLM call from the
client flows through _RedactingChat upstream of this server.

Run as:
    uv run python -m cookbooks.api.mcp_server

Or via .claude.json MCP config — see docs/runbook-mcp.md.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cookbooks._shared.tools.cypher_tools import cypher_read_only as _cypher_read_only
from cookbooks._shared.tools.graph_traversal import (
    evidence_for as _evidence_for,
    neighbors as _neighbors,
)
from cookbooks._shared.tools.merchant_resolve import merchant_resolve as _merchant_resolve
from cookbooks._shared.tools.sql_tools import sql_read_only as _sql_read_only


server = FastMCP("openclaw")


@server.tool()
def cypher_read_only(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query against Neo4j. Returns up to 1000 rows."""
    return _cypher_read_only.invoke({"query": query, "params": params or {}})


@server.tool()
def sql_read_only(query: str, params: list | None = None) -> list[dict]:
    """Execute a read-only SQL query against Postgres. Returns up to 1000 rows."""
    return _sql_read_only.invoke({"query": query, "params": params or []})


@server.tool()
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical Merchant IDs."""
    return _merchant_resolve.invoke({"query": query, "k": k})


@server.tool()
def evidence_for(node_id: str, k: int = 10) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to the given node."""
    return _evidence_for.invoke({"node_id": node_id, "k": k})


@server.tool()
def neighbors(node_id: str, depth: int = 1) -> dict:
    """Return the local subgraph around `node_id` to `depth` hops."""
    return _neighbors.invoke({"node_id": node_id, "depth": depth})


TOOL_NAMES = (
    "cypher_read_only", "sql_read_only", "merchant_resolve",
    "evidence_for", "neighbors",
)


def main() -> None:
    """Entry point for `python -m cookbooks.api.mcp_server`."""
    server.run()


if __name__ == "__main__":
    main()
