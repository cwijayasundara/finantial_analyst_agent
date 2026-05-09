"""Parses object_types.yaml / link_types.yaml / action_types.yaml into typed
Pydantic models and provides a `validate_link` helper used by `compile_graph`.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ONT_DIR = Path(__file__).parent


class ObjectType(BaseModel):
    id: str
    description: str = ""


class LinkType(BaseModel):
    id: str
    from_types: list[str] = Field(alias="from")
    to_types: list[str] = Field(alias="to")

    model_config = {"populate_by_name": True}


class ActionType(BaseModel):
    id: str
    description: str = ""
    function: str
    scopes: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    object_types: list[ObjectType]
    link_types: list[LinkType]
    action_types: list[ActionType]


@cache
def load_ontology() -> Ontology:
    """Load and validate the three ontology YAML files. Cached per process."""
    object_types = [
        ObjectType(**d) for d in yaml.safe_load((ONT_DIR / "object_types.yaml").read_text())
    ]
    link_types = [
        LinkType(**d) for d in yaml.safe_load((ONT_DIR / "link_types.yaml").read_text())
    ]
    action_types = [
        ActionType(**d) for d in yaml.safe_load((ONT_DIR / "action_types.yaml").read_text())
    ]
    return Ontology(
        object_types=object_types,
        link_types=link_types,
        action_types=action_types,
    )


def validate_link(ont: Ontology, link_id: str, from_type: str, to_type: str) -> bool:
    """Return True if (from_type)-[link_id]->(to_type) is permitted."""
    by_id = {l.id: l for l in ont.link_types}
    if link_id not in by_id:
        raise KeyError(f"Unknown link type {link_id!r}")
    link = by_id[link_id]
    return from_type in link.from_types and to_type in link.to_types
