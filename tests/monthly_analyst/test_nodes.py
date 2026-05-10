"""Integration tests for monthly_analyst nodes (Tasks 6-11)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.monthly_analyst.nodes.compute_rollups import compute_rollups_node
from cookbooks.monthly_analyst.nodes.detect_anomalies import detect_anomalies_node
from cookbooks.monthly_analyst.nodes.draft_memo import draft_memo_node
from cookbooks.monthly_analyst.nodes.lint_memo import lint_memo_node
from cookbooks.monthly_analyst.nodes.load_period import load_period_node
from cookbooks.monthly_analyst.nodes.publish import publish_node


@pytest.fixture
def april_2025_ledger(tmp_workspace: Path):
    """Fixture ledger with one credit-card statement + 5 transactions."""
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES "
            "('a_credit','Credit','credit')"
        )
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('stmt_credit_2025_04','a_credit','2025-04-01','2025-04-30',"
            "'apr.pdf','aaaa','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) VALUES "
            "('tesco','Tesco',1),('costa','Costa',3),('amazon','Amazon',8)"
        )
        rows = [
            ("t1", "2025-04-05", "-25.00", "TESCO",   "tesco",  1),
            ("t2", "2025-04-10", "-50.00", "TESCO",   "tesco",  1),
            ("t3", "2025-04-12", "-30.00", "TESCO",   "tesco",  1),
            ("t4", "2025-04-20", "-4.50",  "COSTA",   "costa",  3),
            ("t5", "2025-04-25", "-100.00","AMAZON",  "amazon", 8),
        ]
        for r in rows:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "merchant_id,category_id,statement_id,account_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                list(r) + ["stmt_credit_2025_04", "a_credit"],
            )
    finally:
        conn.close()
    return tmp_workspace


def test_load_period_pulls_statement_and_count(april_2025_ledger):
    state = load_period_node({"period": "2025_04"})
    assert state["transactions_count"] == 5
    assert len(state["statements"]) == 1
    assert state["statements"][0]["id"] == "stmt_credit_2025_04"


def test_compute_rollups_populates_state(april_2025_ledger):
    state = load_period_node({"period": "2025_04"})
    state = compute_rollups_node(state)
    cats = state["category_totals"]
    assert cats[0].category == "groceries"  # 25+50+30 = 105 wins
    merchs = state["merchant_totals"]
    # tesco: 25+50+30=£105 (3 txn), amazon: £100 (1 txn), costa: £4.50
    assert merchs[0].merchant_id == "tesco"
    assert merchs[1].merchant_id == "amazon"
    assert "a_credit" in state["account_balance_delta"]


def test_detect_anomalies_runs_clean_on_simple_ledger(april_2025_ledger):
    state = {"period": "2025_04"}
    state = detect_anomalies_node(state)
    # No subscriptions and no historical data → no findings
    assert state["findings"] == []


def test_draft_memo_template_mode(april_2025_ledger):
    state = load_period_node({"period": "2025_04"})
    state = compute_rollups_node(state)
    state = detect_anomalies_node(state)
    state = draft_memo_node(state)
    body = state["draft_body"]
    assert "Monthly Memo · April 2025" in body
    # Citations include the statement and the top merchants as wikilinks
    assert "[[merchant_amazon]]" in body
    assert "[[merchant_tesco]]" in body


def test_lint_memo_passes_when_template_drives_values(april_2025_ledger):
    state = load_period_node({"period": "2025_04"})
    state = compute_rollups_node(state)
    state = detect_anomalies_node(state)
    state = draft_memo_node(state)
    state = lint_memo_node(state)
    assert state.get("errors", []) == []
    assert state["lint_findings"] == []


def test_publish_writes_memo_page(april_2025_ledger):
    state = load_period_node({"period": "2025_04"})
    state = compute_rollups_node(state)
    state = detect_anomalies_node(state)
    state = draft_memo_node(state)
    state = lint_memo_node(state)
    state = publish_node(state)
    assert state["memo_page_id"] == "memo_2025_04"
    s = load_settings()
    page = s.paths.wiki / "memos" / "memo_2025_04.md"
    assert page.exists()
    body = page.read_text()
    assert "Monthly Memo · April 2025" in body


def test_publish_short_circuits_on_lint_error(april_2025_ledger, monkeypatch):
    state = {
        "period": "2025_04",
        "draft_body": "Mystery £999.99 charge",
        "draft_citations": [],
        "draft_cited_values": [],
    }
    state = lint_memo_node(state)
    assert state["errors"]
    state = publish_node(state)
    assert "memo_page_id" not in state
