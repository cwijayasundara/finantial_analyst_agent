"""Tests for the local sentence-transformers embedding helper."""
from __future__ import annotations

import math

from cookbooks._shared.embeddings import EMBEDDING_DIM, encode, encode_batch


def test_encode_returns_384d_vector():
    vec = encode("Costco")
    assert isinstance(vec, list)
    assert len(vec) == EMBEDDING_DIM
    assert all(isinstance(x, float) for x in vec)


def test_encode_is_normalized():
    """normalize_embeddings=True means L2 norm == 1."""
    vec = encode("Costco")
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-3


def test_encode_empty_returns_zero_vector():
    vec = encode("")
    assert len(vec) == EMBEDDING_DIM
    assert all(x == 0.0 for x in vec)


def test_encode_batch_returns_list_of_vectors():
    texts = ["Costco", "Tesco", "Amazon"]
    vecs = encode_batch(texts)
    assert len(vecs) == 3
    assert all(len(v) == EMBEDDING_DIM for v in vecs)


def test_encode_batch_empty():
    assert encode_batch([]) == []


def test_similar_strings_have_high_cosine_similarity():
    """Sanity check: 'Costco' and 'COSTCO WAREHOUSE' should be close."""
    a = encode("Costco")
    b = encode("COSTCO WAREHOUSE")
    c = encode("library")  # unrelated
    # Cosine similarity = dot product when vectors are unit-normalized.
    sim_ab = sum(x * y for x, y in zip(a, b))
    sim_ac = sum(x * y for x, y in zip(a, c))
    assert sim_ab > sim_ac, (
        f"expected Costco~COSTCO ({sim_ab:.3f}) > Costco~library ({sim_ac:.3f})"
    )
    assert sim_ab > 0.5, f"Costco/COSTCO similarity {sim_ab:.3f} surprisingly low"
