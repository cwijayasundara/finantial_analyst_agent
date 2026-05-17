"""Fails any PR that edits the ontology without regenerating artefacts.

Each generator is deterministic and pure — calling it twice produces
the same bytes. This test asserts that the bytes the generator would
produce RIGHT NOW match the bytes committed to the repo. If they
don't, the engineer forgot to run:

    uv run openclaw-gen-ontology

The fix is always: run the command, commit the resulting diff.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.ontology.gen_init_cypher import (
    OUTPUT_PATH as INIT_CYPHER_PATH,
    generate_init_cypher,
)
from cookbooks._shared.ontology.gen_pydantic import (
    OUTPUT_PATH as PYDANTIC_PATH,
    generate_pydantic,
)
from cookbooks._shared.ontology.gen_schema_prompt import (
    OUTPUT_PATH as SCHEMA_PROMPT_PATH,
    generate_schema_prompt,
)


@pytest.mark.parametrize(
    "path, generator, regen_cmd",
    [
        (INIT_CYPHER_PATH, generate_init_cypher, "uv run openclaw-gen-ontology"),
        (PYDANTIC_PATH, generate_pydantic, "uv run openclaw-gen-ontology"),
        (SCHEMA_PROMPT_PATH, generate_schema_prompt, "uv run openclaw-gen-ontology"),
    ],
    ids=["init_cypher", "pydantic_models", "schema_prompt"],
)
def test_artefact_matches_generator(path: Path, generator, regen_cmd: str):
    expected = generator()
    assert path.exists(), f"missing artefact {path} — run `{regen_cmd}`"
    actual = path.read_text()
    assert actual == expected, (
        f"\n{path} is stale.\n"
        f"Run: {regen_cmd}\n"
        f"Then commit the diff.\n"
    )
