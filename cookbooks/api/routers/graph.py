"""Graph snapshot endpoint — serves the JSONL snapshot as JSON."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.config import load_settings

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/snapshot")
def snapshot(
    type: str | None = Query(None, description="filter nodes by type"),
    limit: int = Query(2000, ge=1, le=20000),
) -> dict:
    settings = load_settings()
    snap = settings.paths.graph_snapshot
    if not snap.exists():
        raise HTTPException(
            status_code=404,
            detail="graph snapshot not built yet (run statement_ingester backfill)",
        )
    nodes: list[dict] = []
    edges: list[dict] = []
    with snap.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") == "node":
                if type and rec.get("type") != type:
                    continue
                nodes.append(rec)
            elif rec.get("kind") == "edge":
                edges.append(rec)
    if len(nodes) > limit:
        nodes = nodes[:limit]
    keep = {n["id"] for n in nodes}
    edges = [e for e in edges if e["from"] in keep and e["to"] in keep]
    return {
        "nodes": nodes, "edges": edges,
        "node_count": len(nodes), "edge_count": len(edges),
    }
