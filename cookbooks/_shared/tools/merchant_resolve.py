"""Merchant resolution — hybrid (full-text + vector) with RRF blend.

The agent calls this BEFORE writing any merchant-filtered Cypher. Hands
the user's free text (e.g. "Costco", "AMZN MKTP", "tesco stores") and
gets back the canonical Merchant IDs ranked by a Reciprocal-Rank-Fusion
blend of two signals:

  1. **Full-text** — Neo4j's `merchant_fulltext` Lucene index over
     canonical_name + aliases. Strong on exact and partial word matches.
  2. **Vector** — cosine similarity over `Merchant.embedding` (384-d
     MiniLM-L6-v2). Strong on semantic / fuzzy matches and typos.

RRF (rank = 1 / (RRF_K + position)) is the standard hybrid-search blend
— robust because it works on ranks, not raw scores from incompatible
distributions.

The vector branch requires `Merchant.embedding` to be populated, which
`compile_neo4j` does at compile time. If embeddings aren't present yet
(empty graph, or pre-Tier-3 compile), the vector branch returns no
results and the function degrades gracefully to full-text only.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from cookbooks._shared.embeddings import encode
from cookbooks._shared.neo4j_client import session


_log = logging.getLogger(__name__)

# RRF parameter — standard default. Higher = more aggressive blending
# (later ranks contribute more); lower = winner-takes-all (top-1 dominates).
RRF_K = 60

_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('merchant_fulltext', $query, {limit: $k})
YIELD node, score
RETURN node.id AS id, node.canonical_name AS canonical_name, score
ORDER BY score DESC
"""

_VECTOR_QUERY = """
CALL db.index.vector.queryNodes('merchant_canonical_name_vec', $k, $embedding)
YIELD node, score
RETURN node.id AS id, node.canonical_name AS canonical_name, score
"""


def _fulltext_hits(query: str, k: int) -> list[dict]:
    with session(read_only=True) as s:
        result = s.run(_FULLTEXT_QUERY, {"query": query, "k": k})
        return [dict(r) for r in result]


def _vector_hits(query: str, k: int) -> list[dict]:
    """Vector search via Neo4j's native vector index.

    Returns [] if the index is empty / missing or if the model fails to
    encode (the function caller already validated query is non-empty).
    """
    try:
        embedding = encode(query)
    except Exception as e:
        _log.warning("merchant_resolve vector branch: encode failed (%s); "
                     "falling back to fulltext only", e)
        return []
    try:
        with session(read_only=True) as s:
            result = s.run(_VECTOR_QUERY, {"k": k, "embedding": embedding})
            return [dict(r) for r in result]
    except Exception as e:
        # Neo4j raises if the vector index isn't populated yet or has
        # the wrong dimensionality. Degrade gracefully — the fulltext
        # branch still returns useful results.
        _log.warning("merchant_resolve vector branch: query failed (%s); "
                     "falling back to fulltext only", e)
        return []


def _rrf_blend(*ranked_lists: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion across N ranked lists.

    rank_score(doc) = sum over each list L of: 1 / (RRF_K + position_in_L)
    Items absent from a list contribute zero from that list.

    Returns hits sorted by descending RRF score, with the highest-scoring
    appearance's metadata (id, canonical_name).
    """
    scores: dict[str, float] = {}
    seen: dict[str, dict] = {}
    for ranked in ranked_lists:
        for pos, hit in enumerate(ranked):
            doc_id = hit["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + pos)
            if doc_id not in seen:
                seen[doc_id] = hit
    fused = [
        {"id": doc_id,
         "canonical_name": seen[doc_id]["canonical_name"],
         "score": score}
        for doc_id, score in scores.items()
    ]
    fused.sort(key=lambda h: h["score"], reverse=True)
    return fused


@tool
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical Merchant IDs.

    Hybrid: Neo4j full-text index (Lucene over canonical_name + aliases)
    blended with cosine similarity over Merchant.embedding (384-d
    MiniLM-L6-v2) via Reciprocal Rank Fusion. Returns up to `k` hits as
    `{id, canonical_name, score}` sorted by RRF score, descending.

    Falls back to fulltext-only if the vector index hasn't been populated
    yet (e.g., before the first Tier-3 compile_neo4j run).

    Call this BEFORE writing merchant-filtered Cypher so the agent can
    cite the canonical ID rather than a free-text guess.
    """
    if not query.strip():
        return []

    # Pull `k` from each side, then RRF-blend and cap. Pulling extra from
    # each side gives the blend more material to work with at the cost of
    # one extra Cypher hit on each branch.
    pool = max(k * 3, 10)
    ft_hits = _fulltext_hits(query, pool)
    vec_hits = _vector_hits(query, pool)
    fused = _rrf_blend(ft_hits, vec_hits)[:k]

    _log.info(
        "merchant_resolve(%r, k=%d) -> %d hits (fulltext=%d, vector=%d)",
        query, k, len(fused), len(ft_hits), len(vec_hits),
    )
    return fused
