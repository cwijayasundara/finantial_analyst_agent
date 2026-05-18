from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.actions import (
    publish_monthly_memo,
    upsert_merchant,
)
from cookbooks._shared.qa_tools import (
    merge_merchants,
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
    # compile_graph() removed in PR 4.3 — wiki population is enough for
    # these tests; the deepagent path uses cypher_read_only against Neo4j
    # which has its own dedicated tests.
    return tmp_workspace


# TestQueryGraph removed in PR 4.3 — the Kuzu query_graph @tool is gone.
# Cypher-against-Neo4j coverage lives in tests/_shared/tools/test_cypher_tools.py.


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
