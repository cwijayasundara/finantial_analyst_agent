from __future__ import annotations

import pytest

from cookbooks._shared.ontology.loader import (
    Ontology,
    load_ontology,
    validate_link,
)


def test_load_ontology_returns_typed_object():
    ont = load_ontology()
    assert isinstance(ont, Ontology)
    assert {o.id for o in ont.object_types} >= {
        "Account", "Statement", "Transaction", "Merchant", "Category",
        "Subscription", "Memo", "Decision", "Annotation",
    }


def test_load_ontology_link_types_have_endpoints():
    ont = load_ontology()
    by_id = {l.id: l for l in ont.link_types}
    assert "Transaction" in by_id["at_merchant"].from_types
    assert "Merchant" in by_id["at_merchant"].to_types


def test_load_ontology_action_types_have_functions():
    ont = load_ontology()
    by_id = {a.id: a for a in ont.action_types}
    assert by_id["publish_monthly_memo"].function.endswith(":publish_monthly_memo")


def test_validate_link_accepts_valid_shape():
    ont = load_ontology()
    assert validate_link(ont, "at_merchant", "Transaction", "Merchant") is True


def test_validate_link_rejects_invalid_shape():
    ont = load_ontology()
    assert validate_link(ont, "at_merchant", "Memo", "Merchant") is False


def test_validate_link_rejects_unknown_link():
    ont = load_ontology()
    with pytest.raises(KeyError):
        validate_link(ont, "no_such_link", "Memo", "Memo")


def test_object_type_has_embedding_field():
    """Merchant has an embedding field; Account does not."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert by_id["Merchant"].embedding_field == "canonical_name"
    assert by_id["Account"].embedding_field is None


def test_object_type_has_text_search_fields():
    """Merchant declares the fields that go into a full-text index."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert "canonical_name" in by_id["Merchant"].text_search_fields
    assert "aliases" in by_id["Merchant"].text_search_fields


def test_ontology_has_meta():
    """meta.yaml supplies schema version + embedding model."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    assert ont.meta.schema_version == 1
    assert ont.meta.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert ont.meta.embedding_dim == 384


def test_object_type_id_template():
    """Each ObjectType has an id_template documenting its canonical ID shape."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert by_id["Merchant"].id_template == "merchant::<canonical-slug>"
    assert by_id["Transaction"].id_template == "tx::<statement-id>::<row>"
