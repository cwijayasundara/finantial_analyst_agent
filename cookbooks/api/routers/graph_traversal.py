"""Graph traversal endpoints — node, neighbors, evidence.

Thin pass-through over cookbooks/_shared/tools/graph_traversal.py.
Neo4j-backed; replaces the older Kuzu-JSONL snapshot router (deleted
in PR 4.3).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.tools.graph_traversal import (
    evidence_for,
    get_node,
    neighbors,
)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/node/{node_id:path}")
def graph_node(node_id: str) -> dict:
    """Return one node by id."""
    result = get_node.invoke({"node_id": node_id})
    if result is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")
    return result


@router.get("/neighbors/{node_id:path}")
def graph_neighbors(
    node_id: str,
    depth: int = Query(1, ge=1, le=4),
) -> dict:
    """Return the local subgraph around `node_id` to `depth` hops.

    Returns ``{nodes: [{id, label}], edges: [{source, target, type}]}``.
    """
    try:
        return neighbors.invoke({"node_id": node_id, "depth": depth})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/evidence/{node_id:path}")
def graph_evidence(
    node_id: str,
    k: int = Query(10, ge=1, le=100),
) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to `node_id`."""
    return evidence_for.invoke({"node_id": node_id, "k": k})
