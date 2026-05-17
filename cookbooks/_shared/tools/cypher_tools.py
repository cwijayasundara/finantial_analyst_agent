"""Read-only Cypher tools for the openclaw agent.

`cypher_read_only(query, params)` is the primary escape hatch — the agent
writes Cypher freehand and we apply guards:
  1. reject_write_keywords  (token-level, ignores string literals)
  2. EXPLAIN first; reject if dbHits > MAX_DB_HITS
  3. enforce_implicit_limit (CYPHER_DEFAULT_LIMIT rows max)
  4. tx timeout = CYPHER_TIMEOUT_S

`cypher_explain` returns the plan, never runs the query.

Both are decorated as @tool so deepagents (and langchain.agents) can
bind them directly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session
from cookbooks._shared.tools.safety import (
    CypherTooExpensive,
    enforce_implicit_limit,
    reject_write_keywords,
)


CYPHER_DEFAULT_LIMIT = int(os.environ.get("PFH_CYPHER_DEFAULT_LIMIT", "1000"))
CYPHER_TIMEOUT_S = int(os.environ.get("PFH_CYPHER_TIMEOUT_S", "5"))
MAX_DB_HITS = int(os.environ.get("PFH_CYPHER_MAX_DB_HITS", "10000000"))

_log = logging.getLogger(__name__)


def _explain_plan(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run EXPLAIN, return the plan as a dict. Does NOT execute the query.

    The neo4j driver (v6+) returns summary.plan as a plain dict with keys:
      operatorType, args (dict of planner args), identifiers, children.
    """
    with session(read_only=True) as s:
        result = s.run(f"EXPLAIN {query}", parameters=params or {})
        summary = result.consume()
        plan = summary.plan
        if plan is None:
            return {"operator_type": "Unknown", "db_hits": 0}
        args = plan.get("args", {}) if isinstance(plan, dict) else {}
        return {
            "operator_type": plan.get("operatorType", "Unknown") if isinstance(plan, dict) else getattr(plan, "operator_type", "Unknown"),
            "db_hits": args.get("DbHits", 0),
            "estimated_rows": args.get("EstimatedRows", 0),
            "identifiers": plan.get("identifiers", []) if isinstance(plan, dict) else list(getattr(plan, "identifiers", [])),
        }


@tool
def cypher_read_only(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query against Neo4j.

    Returns up to CYPHER_DEFAULT_LIMIT rows as a list of dicts. Rejects
    any write keyword (CREATE / MERGE / DELETE / SET / REMOVE / DETACH /
    DROP / APOC writes). Pre-flights the query with EXPLAIN and rejects
    if the planner estimates more than MAX_DB_HITS. Runs under a
    CYPHER_TIMEOUT_S timeout.
    """
    reject_write_keywords(query)
    params = params or {}

    plan = _explain_plan(query, params)
    if plan["db_hits"] and plan["db_hits"] > MAX_DB_HITS:
        raise CypherTooExpensive(
            f"query rejected: EXPLAIN dbHits {plan['db_hits']} > "
            f"max {MAX_DB_HITS} (raise PFH_CYPHER_MAX_DB_HITS to override)"
        )

    bounded = enforce_implicit_limit(query, default_limit=CYPHER_DEFAULT_LIMIT)
    with session(read_only=True) as s:
        result = s.run(bounded, parameters=params, timeout=CYPHER_TIMEOUT_S)
        rows = [dict(r) for r in result]
    _log.info("cypher_read_only ok: %d rows, plan=%s", len(rows), plan["operator_type"])
    return rows


@tool
def cypher_explain(query: str, params: dict | None = None) -> dict:
    """Return the Neo4j plan for `query` without executing it.

    Use this to estimate cost before running an expensive query.
    Same write-keyword guard as cypher_read_only.
    """
    reject_write_keywords(query)
    return _explain_plan(query, params or {})
