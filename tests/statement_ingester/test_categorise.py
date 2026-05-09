from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.statement_ingester.nodes.categorise import (
    categorise_node,
    load_rules_cache,
    save_rules_cache,
)
from cookbooks.statement_ingester.schemas import CategorisationResult


def _stub_llm_returning(result: CategorisationResult):
    structured = MagicMock()
    structured.invoke.return_value = result
    chat = MagicMock()
    chat.with_structured_output.return_value = structured
    return chat


def test_load_save_rules_cache_roundtrip(tmp_workspace: Path):
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    cache = load_rules_cache()
    assert cache["TESCO STORES 4521"] == ("tesco", "groceries")


def test_categorise_node_uses_cache_first(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    with patch("cookbooks.statement_ingester.nodes.categorise.build_chat_model") as mc:
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521"],
        })
        mc.assert_not_called()
    assert any(c.merchant_canonical.lower() == "tesco" for c in state["categorised"])


def test_categorise_node_calls_llm_only_for_unknown(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    fake = CategorisationResult(
        merchant_canonical="Starbucks", category="dining",
        confidence=0.95, reasoning_short="coffee chain",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521", "STARBUCKS 11A"],
        })
    cats = {c.merchant_canonical.lower() for c in state["categorised"]}
    assert "starbucks" in cats
    cache = load_rules_cache()
    assert "STARBUCKS 11A" in cache


def test_categorise_node_writes_merchant_pages(tmp_workspace: Path):
    init_schema()
    fake = CategorisationResult(
        merchant_canonical="Netflix", category="subscription",
        confidence=0.99, reasoning_short="streaming service",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        categorise_node({"new_merchants": ["NETFLIX SUBS"]})
    s = load_settings()
    pages = list((s.paths.wiki / "merchants").glob("merchant_*.md"))
    assert pages, "expected at least one merchant page written"


def test_categorise_node_handles_empty_input(tmp_workspace: Path):
    init_schema()
    state = categorise_node({"new_merchants": []})
    assert state["categorised"] == []
