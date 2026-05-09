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
