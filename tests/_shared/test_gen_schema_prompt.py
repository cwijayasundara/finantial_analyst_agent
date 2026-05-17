"""Tests for the agent schema-prompt generator."""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology.gen_schema_prompt import generate_schema_prompt


def test_contains_schema_section_header():
    md = generate_schema_prompt()
    assert "## SCHEMA" in md
    assert "## RELATIONSHIPS" in md
    assert "## ACTIONS" in md


def test_relationships_are_in_cypher_form():
    md = generate_schema_prompt()
    # link_id `at_merchant` should appear as AT_MERCHANT in the prompt.
    assert "AT_MERCHANT" in md
    assert "IN_STATEMENT" in md
    assert "CATEGORISED_AS" in md


def test_object_types_listed_with_id_template():
    md = generate_schema_prompt()
    assert "Merchant" in md
    assert "merchant::<canonical-slug>" in md
    assert "Transaction" in md
    assert "tx::<statement-id>::<row>" in md


def test_action_types_listed_with_scopes():
    md = generate_schema_prompt()
    # ActionTypes carry a `scopes` field; the prompt should call out the
    # available action ids so the agent knows what's possible.
    assert "## ACTIONS" in md


def test_output_is_deterministic():
    a = generate_schema_prompt()
    b = generate_schema_prompt()
    assert a == b


def test_committed_artefact_matches_generator():
    committed = (
        Path(__file__).resolve().parents[2]
        / "cookbooks" / "_shared" / "skills" / "_generated_schema.md"
    )
    assert committed.exists()
    assert committed.read_text() == generate_schema_prompt()
