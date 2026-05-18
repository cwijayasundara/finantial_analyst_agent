"""Local embedding model — sentence-transformers/all-MiniLM-L6-v2.

384-dimensional cosine-similarity embeddings used by:
  - `compile_neo4j` to populate `Merchant.embedding` (and Concept.embedding
    when the Concept layer lands)
  - `merchant_resolve` for the vector branch of its hybrid lookup

The model is loaded lazily on first call and cached for the process
lifetime. First load downloads ~80MB to ~/.cache/huggingface/ on the
first machine that uses it; subsequent loads are sub-second.

No PII leaves the host — sentence-transformers runs entirely locally.
"""
from __future__ import annotations

from functools import cache

from sentence_transformers import SentenceTransformer


_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


@cache
def _model() -> SentenceTransformer:
    """Return the process-wide singleton model. Build it on first call."""
    return SentenceTransformer(_MODEL_NAME)


def encode(text: str) -> list[float]:
    """Encode a single string. Returns a 384-d list of floats."""
    if not text:
        return [0.0] * EMBEDDING_DIM
    vec = _model().encode(text, convert_to_numpy=True, normalize_embeddings=True)
    return vec.tolist()


def encode_batch(texts: list[str]) -> list[list[float]]:
    """Encode a list of strings. Returns a list of 384-d vectors.

    Batching is ~10x faster than encoding one at a time for non-trivial
    corpora. Pass everything you have in one call.
    """
    if not texts:
        return []
    safe = [t or "" for t in texts]
    arr = _model().encode(safe, convert_to_numpy=True, normalize_embeddings=True,
                          batch_size=32, show_progress_bar=False)
    return arr.tolist()
