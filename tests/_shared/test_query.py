from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.compile_graph import compile_graph
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    upsert_merchant,
    upsert_statement,
)
from cookbooks._shared.query import QueryRejectedError, query_graph


@pytest.fixture
def graph_with_merchants(tmp_workspace: Path):
    """Compile a tiny graph with a couple of merchants and a statement."""
    init_schema()
    upsert_merchant(
        actor="ingester", merchant_id="amazon",
        canonical_name="Amazon", category="other", aliases=["AMZN", "amazon.co.uk"],
    )
    upsert_merchant(
        actor="ingester", merchant_id="costa",
        canonical_name="Costa", category="dining", aliases=["COSTA COFFEE"],
    )
    upsert_statement(
        actor="ingester", statement_id="stmt_x",
        account_id="a_credit", period_start="2025-04-01", period_end="2025-04-30",
        source_pdf="x.pdf", sha256="d" * 64, parser_used="docling",
    )
    compile_graph()
    return tmp_workspace


class TestSafetyChecks:
    @pytest.mark.parametrize("forbidden", [
        "MATCH (n) DELETE n",
        "match (n) delete n",
        "CREATE (m:Merchant {id: 'x'})",
        "MERGE (m:Merchant)",
        "MATCH (n) SET n.x = 1 RETURN n",
        "DROP TABLE Merchant",
        "ALTER TABLE Merchant ADD COLUMN x VARCHAR",
    ])
    def test_rejects_writes(self, forbidden):
        with pytest.raises(QueryRejectedError):
            query_graph(forbidden)

    def test_allows_match_return(self, graph_with_merchants):
        rows = query_graph(
            "MATCH (m:Entity) WHERE m.type = 'Merchant' RETURN m.id LIMIT 10"
        )
        assert len(rows) >= 2

    def test_keyword_in_string_literal_is_ok(self, graph_with_merchants):
        # Strings containing 'create' inside the query body are not commands
        rows = query_graph(
            "MATCH (m:Entity) WHERE m.id = 'costa' RETURN m.id"
        )
        assert len(rows) == 1

    def test_lowercase_keywords_rejected(self):
        with pytest.raises(QueryRejectedError, match="forbidden"):
            query_graph("match (n) delete n")


class TestRowCap:
    def test_caps_unbounded_query(self, graph_with_merchants, monkeypatch):
        monkeypatch.setenv("PFH_QA_ROW_LIMIT", "1")
        rows = query_graph(
            "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id"
        )
        assert len(rows) == 1

    def test_user_supplied_limit_is_respected(self, graph_with_merchants):
        rows = query_graph(
            "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id LIMIT 1"
        )
        assert len(rows) == 1

    def test_returns_list_of_dicts(self, graph_with_merchants):
        rows = query_graph(
            "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id LIMIT 1"
        )
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)


def test_no_kuzu_db_returns_empty(tmp_workspace: Path):
    # Fresh workspace, no compile_graph run → empty result, no crash
    init_schema()
    rows = query_graph("MATCH (m:Entity) RETURN m.id")
    assert rows == []
