"""Graph traversal tools — evidence_for, neighbors.

Both are pure-Cypher reads exposed via the MCP server. They use simple
MATCH patterns (no APOC); compatible with stock Neo4j Community.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session


_log = logging.getLogger(__name__)

_EVIDENCE_QUERY = """
MATCH (anchor {id: $node_id})-[]-(n:Transaction)
RETURN n.id AS id, n.date AS date, n.amount AS amount,
       n.raw_description AS raw_description
ORDER BY n.date DESC
LIMIT $k
"""


@tool
def evidence_for(node_id: str, k: int = 10) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to the given node.

    For a Merchant, this returns the transactions at that merchant.
    For a Category, the transactions in that category. For a Statement,
    the transactions on it.

    Most recent transactions first.
    """
    with session(read_only=True) as s:
        result = s.run(_EVIDENCE_QUERY, {"node_id": node_id, "k": k})
        rows = [dict(r) for r in result]
    _log.info("evidence_for(%s, k=%d) -> %d rows", node_id, k, len(rows))
    return rows


@tool
def neighbors(node_id: str, depth: int = 1) -> dict[str, list[dict]]:
    """Return the local subgraph around `node_id` to `depth` hops.

    Returns ``{nodes: [{id, label}], edges: [{source, target, type}]}``.
    Suitable for handing to react-force-graph in the UI (Plan 4) or
    inspecting in Claude Code via the MCP server.
    """
    if depth < 1 or depth > 4:
        raise ValueError(f"depth must be 1..4 (got {depth})")

    # Cypher path-variable depth can't be parameterised; safe to format
    # because we validated the range above.
    query = f"""
MATCH (anchor {{id: $node_id}})
OPTIONAL MATCH path = (anchor)-[*1..{depth}]-(other)
WITH anchor, collect(distinct other) + [anchor] AS all_nodes,
     collect(distinct path) AS paths
UNWIND all_nodes AS n
WITH paths, collect(distinct {{id: n.id, label: head(labels(n))}}) AS nodes
UNWIND paths AS p
UNWIND relationships(p) AS r
WITH nodes, collect(distinct {{
    source: startNode(r).id,
    target: endNode(r).id,
    type: type(r)
}}) AS edges
RETURN nodes, edges
"""

    with session(read_only=True) as s:
        result = s.run(query, {"node_id": node_id})
        rec = result.single()
    if rec is None:
        return {"nodes": [], "edges": []}
    return {"nodes": rec["nodes"], "edges": rec["edges"]}
