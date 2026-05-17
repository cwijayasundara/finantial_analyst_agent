"""Tests for ontology naming-convention helpers."""
from __future__ import annotations

import pytest

from cookbooks._shared.ontology._naming import (
    link_id_to_cypher_rel,
    object_id_to_label,
    object_id_to_constraint_name,
    object_id_to_vector_index_name,
    object_id_to_fulltext_index_name,
)


@pytest.mark.parametrize("link_id, expected", [
    ("at_merchant", "AT_MERCHANT"),
    ("in_statement", "IN_STATEMENT"),
    ("categorised_as", "CATEGORISED_AS"),
    ("parent_of", "PARENT_OF"),
])
def test_link_id_to_cypher_rel(link_id, expected):
    assert link_id_to_cypher_rel(link_id) == expected


def test_object_id_to_label_is_identity():
    # ObjectType ids are already PascalCase in the YAML.
    assert object_id_to_label("Merchant") == "Merchant"
    assert object_id_to_label("NetWorthSnapshot") == "NetWorthSnapshot"


def test_object_id_to_constraint_name():
    assert object_id_to_constraint_name("Merchant") == "merchant_id_unique"


def test_object_id_to_vector_index_name():
    assert object_id_to_vector_index_name("Merchant", "canonical_name") == "merchant_canonical_name_vec"


def test_object_id_to_fulltext_index_name():
    assert object_id_to_fulltext_index_name("Merchant") == "merchant_fulltext"
