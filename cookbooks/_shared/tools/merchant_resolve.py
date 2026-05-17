"""Merchant resolution — full-text path.

The agent calls this BEFORE writing any merchant-filtered Cypher. Hands
the user's free text (e.g. "Costco", "AMZN MKTP", "tesco stores") and
gets back the canonical Merchant IDs ranked by Lucene score.

Future (Plan 4 / Concept layer): add a vector branch using
Merchant.embedding (which compile_neo4j doesn't populate yet) and
RRF-blend with the full-text path. The function signature stays the
same; only the internals grow.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session


_log = logging.getLogger(__name__)

_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('merchant_fulltext', $query, {limit: $k})
YIELD node, score
RETURN node.id AS id, node.canonical_name AS canonical_name, score
ORDER BY score DESC
"""


@tool
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical Merchant IDs.

    Uses Neo4j's full-text index `merchant_fulltext` (over canonical_name
    + aliases). Returns up to `k` hits as `{id, canonical_name, score}`,
    sorted by descending Lucene score. Empty list if no match.

    Call this BEFORE writing merchant-filtered Cypher so the agent can
    cite the canonical ID rather than a free-text guess.
    """
    if not query.strip():
        return []
    with session(read_only=True) as s:
        result = s.run(_FULLTEXT_QUERY, {"query": query, "k": k})
        hits = [dict(r) for r in result]
    _log.info("merchant_resolve(%r, k=%d) -> %d hits", query, k, len(hits))
    return hits
