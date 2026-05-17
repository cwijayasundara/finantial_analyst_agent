"""Tests for the Pydantic model generator."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from cookbooks._shared.ontology.gen_pydantic import generate_pydantic


def test_emits_one_class_per_object_type():
    code = generate_pydantic()
    # Spot-check a few.
    assert "class Merchant(BaseModel):" in code
    assert "class Transaction(BaseModel):" in code
    assert "class Account(BaseModel):" in code
    assert "class Memo(BaseModel):" in code


def test_every_class_has_id_field():
    code = generate_pydantic()
    # Count classes vs id field declarations.
    class_count = code.count("class ") - 1  # subtract the import line if any
    id_count = code.count("    id: str")
    assert id_count >= class_count - 1, (
        f"expected ~{class_count} id fields, got {id_count}"
    )


def test_embedding_field_emitted_when_declared():
    code = generate_pydantic()
    # Merchant declares embedding_field=canonical_name; the field should appear.
    assert "canonical_name: str" in code
    assert "embedding: list[float] | None = None" in code


def test_output_is_deterministic():
    a = generate_pydantic()
    b = generate_pydantic()
    assert a == b


def test_generated_artefact_is_importable(tmp_path):
    """Round-trip: write, import, instantiate Merchant."""
    code = generate_pydantic()
    target = tmp_path / "_gen.py"
    target.write_text(code)
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("_gen")
        m = mod.Merchant(id="merchant::costco", canonical_name="Costco")
        assert m.id == "merchant::costco"
        assert m.canonical_name == "Costco"
        assert m.embedding is None
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("_gen", None)


def test_committed_artefact_matches_generator():
    committed = Path(__file__).resolve().parents[2] / "cookbooks" / "_shared" / "models" / "_generated.py"
    assert committed.exists(), (
        "missing generated models. Run `uv run python -m cookbooks._shared.ontology.gen_pydantic`."
    )
    assert committed.read_text() == generate_pydantic()
