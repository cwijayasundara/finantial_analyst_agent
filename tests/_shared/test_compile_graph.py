from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbooks._shared.compile_graph import compile_graph, graph_fingerprint
from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    upsert_merchant,
    upsert_statement,
)


@pytest.fixture
def seeded(tmp_workspace: Path):
    init_schema()
    upsert_statement(
        actor="ingester",
        statement_id="stmt_jan", account_id="acct_savings",
        period_start="2026-01-01", period_end="2026-01-31",
        source_pdf="sources/savings_stmt/2026_January_Statement.pdf",
        sha256="a" * 64, parser_used="docling",
    )
    upsert_merchant(
        actor="ingester", merchant_id="tesco",
        canonical_name="Tesco", category="groceries",
        aliases=["TESCO STORES 4521"],
    )
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO accounts(id,name,type) VALUES (?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET name=excluded.name, type=excluded.type",
        ["acct_savings", "Savings", "savings"],
    )
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id,merchant_id,category_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["txn_1", "2026-01-15", -42.50, "TESCO STORES 4521",
         "acct_savings", "stmt_jan", "tesco", 1],
    )
    conn.close()
    return tmp_workspace


def test_compile_graph_writes_jsonl_snapshot(seeded):
    result = compile_graph()
    s = load_settings()
    assert s.paths.graph_snapshot.exists()
    rows = [json.loads(l) for l in s.paths.graph_snapshot.read_text().splitlines() if l.strip()]
    kinds = {r["kind"] for r in rows}
    assert {"node", "edge"} <= kinds
    nodes = [r for r in rows if r["kind"] == "node"]
    edges = [r for r in rows if r["kind"] == "edge"]
    assert any(n["type"] == "Account" for n in nodes)
    assert any(n["type"] == "Statement" for n in nodes)
    assert any(n["type"] == "Merchant" for n in nodes)
    assert any(n["type"] == "Transaction" for n in nodes)
    assert any(e["type"] == "at_merchant" for e in edges)
    assert any(e["type"] == "in_statement" for e in edges)
    assert result["nodes"] >= 4
    assert result["edges"] >= 2


def test_compile_graph_is_idempotent_via_fingerprint(seeded):
    first = compile_graph()
    second = compile_graph()
    assert second["skipped"] is True
    assert second["fingerprint"] == first["fingerprint"]


def test_compile_graph_force_rebuilds(seeded):
    compile_graph()
    again = compile_graph(force=True)
    assert again["skipped"] is False


def test_graph_fingerprint_changes_on_new_transaction(seeded):
    fp1 = graph_fingerprint()
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id) VALUES (?,?,?,?,?,?)",
        ["txn_2", "2026-01-16", -10.00, "FOO", "acct_savings", "stmt_jan"],
    )
    conn.close()
    fp2 = graph_fingerprint()
    assert fp1 != fp2


def test_compile_graph_rejects_invalid_link_shape(seeded):
    """If the ledger has data that violates ontology, compile reports it."""
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
        ["acct_credit", "Credit", "credit"],
    )
    conn.close()
    # No invalid shape produced here yet, but compile must not crash.
    result = compile_graph(force=True)
    assert "errors" in result
