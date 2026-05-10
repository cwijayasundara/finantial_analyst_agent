from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.compile_graph import compile_graph
from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.actions import (
    publish_monthly_memo,
    upsert_merchant,
)
from cookbooks._shared.qa_tools import (
    merge_merchants,
    query_graph,
    read_wiki_page,
)


@pytest.fixture
def populated_workspace(tmp_workspace: Path):
    init_schema()
    upsert_merchant(
        actor="ingester", merchant_id="amazon",
        canonical_name="Amazon", category="other", aliases=["amazon.co.uk"],
    )
    upsert_merchant(
        actor="ingester", merchant_id="costa",
        canonical_name="Costa", category="dining", aliases=[],
    )
    publish_monthly_memo(
        actor="analyst", period="2025_04",
        body_md="# April 2025\n\nSpent £42.00 at [[merchant_costa]].\n",
        citations=["merchant_costa"], confidence=0.9,
    )
    compile_graph()
    return tmp_workspace


class TestQueryGraph:
    def test_returns_rows(self, populated_workspace):
        out = query_graph(
            "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id LIMIT 10"
        )
        assert out["row_count"] >= 2
        assert isinstance(out["rows"], list)

    def test_rejects_writes(self):
        from cookbooks._shared.query import QueryRejectedError
        with pytest.raises(QueryRejectedError):
            query_graph("MATCH (n) DELETE n")


class TestReadWikiPage:
    def test_finds_merchant(self, populated_workspace):
        out = read_wiki_page("merchant_amazon")
        assert out["type"] == "Merchant"
        assert "Amazon" in out["body"]
        assert out["frontmatter"]["canonical_name"] == "Amazon"

    def test_finds_memo(self, populated_workspace):
        out = read_wiki_page("memo_2025_04")
        assert out["type"] == "Memo"
        assert "April 2025" in out["body"]

    def test_unknown_id_returns_error(self, populated_workspace):
        out = read_wiki_page("merchant_does_not_exist")
        assert out["error"] == "not found"

    def test_body_capped(self, populated_workspace):
        out = read_wiki_page("merchant_amazon")
        assert len(out["body"]) <= 4000


class TestMergeMerchants:
    def test_merges_and_returns_target(self, populated_workspace):
        # Add a third merchant to merge from
        upsert_merchant(
            actor="ingester", merchant_id="amzn",
            canonical_name="Amzn", category="other", aliases=["AMZN*X"],
        )
        out = merge_merchants(
            source_merchant_id="amzn",
            target_merchant_id="amazon",
            reason="duplicate brand variant",
        )
        assert out["ok"] is True
        assert out["target_page_id"] == "merchant_amazon"
        assert out["merged"] == {"from": "amzn", "into": "amazon"}

    def test_self_merge_rejected(self, populated_workspace):
        with pytest.raises(ValueError):
            merge_merchants(
                source_merchant_id="amazon",
                target_merchant_id="amazon",
                reason="oops",
            )
