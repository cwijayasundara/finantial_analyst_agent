"""Tests for the Neo4j init.cypher generator."""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology.gen_init_cypher import generate_init_cypher


def test_generates_unique_constraint_per_object_type():
    cypher = generate_init_cypher()
    # Spot-check a few ObjectTypes the ontology defines.
    assert "CREATE CONSTRAINT merchant_id_unique" in cypher
    assert "CREATE CONSTRAINT account_id_unique" in cypher
    assert "CREATE CONSTRAINT transaction_id_unique" in cypher
    # All constraints use the FOR (n:Label) REQUIRE n.id IS UNIQUE form.
    assert "FOR (n:Merchant) REQUIRE n.id IS UNIQUE" in cypher


def test_generates_vector_index_for_embedding_fields():
    cypher = generate_init_cypher()
    assert "CREATE VECTOR INDEX merchant_canonical_name_vec" in cypher
    # Vector dim and similarity are read from the ontology meta + ObjectType.
    assert "`vector.dimensions`: 384" in cypher
    assert "`vector.similarity_function`: 'cosine'" in cypher


def test_generates_fulltext_index_when_text_search_fields_present():
    cypher = generate_init_cypher()
    assert "CREATE FULLTEXT INDEX merchant_fulltext" in cypher
    # Indexes the declared fields.
    assert "[n.canonical_name, n.aliases]" in cypher


def test_no_vector_index_for_object_types_without_embedding():
    cypher = generate_init_cypher()
    # Account has no embedding_field -> no vector index for it.
    # Stronger: no `CREATE VECTOR INDEX account_` line.
    for line in cypher.splitlines():
        if line.strip().startswith("CREATE VECTOR INDEX"):
            assert "account_" not in line.lower()


def test_writes_meta_singleton():
    cypher = generate_init_cypher()
    assert "MERGE (m:Meta {id: 'schema'})" in cypher
    assert "schema_version = 1" in cypher
    assert "embedding_model = 'sentence-transformers/all-MiniLM-L6-v2'" in cypher
    assert "embedding_dim = 384" in cypher


def test_output_is_deterministic():
    """Two calls must produce byte-identical output (consistency test depends on this)."""
    a = generate_init_cypher()
    b = generate_init_cypher()
    assert a == b


def test_committed_artefact_matches_generator():
    """The committed db/neo4j/init.cypher must equal what the generator emits."""
    committed_path = Path(__file__).resolve().parents[2] / "db" / "neo4j" / "init.cypher"
    assert committed_path.exists(), (
        f"missing generated artefact: {committed_path}. "
        "Run `uv run python -m cookbooks._shared.ontology.gen_init_cypher`."
    )
    assert committed_path.read_text() == generate_init_cypher()
