"""Naming-convention helpers for ontology → Neo4j artefacts.

Single place for the mapping rules so every generator agrees:

  ObjectType id  (PascalCase)  -> Neo4j Label    (PascalCase, identity)
  LinkType id    (snake_case)  -> Cypher REL     (UPPER_SNAKE)
  ObjectType id  -> constraint / index names     (lower_snake + suffix)

Any new naming rule that touches a generator goes here, not inline.
"""
from __future__ import annotations


def link_id_to_cypher_rel(link_id: str) -> str:
    """`at_merchant` -> `AT_MERCHANT`. snake_case -> UPPER_SNAKE."""
    return link_id.upper()


def object_id_to_label(object_id: str) -> str:
    """ObjectType id IS the Neo4j label — identity for now."""
    return object_id


def _to_snake(name: str) -> str:
    """`NetWorthSnapshot` -> `net_worth_snapshot`."""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def object_id_to_constraint_name(object_id: str) -> str:
    return f"{_to_snake(object_id)}_id_unique"


def object_id_to_vector_index_name(object_id: str, field: str) -> str:
    return f"{_to_snake(object_id)}_{field}_vec"


def object_id_to_fulltext_index_name(object_id: str) -> str:
    return f"{_to_snake(object_id)}_fulltext"
