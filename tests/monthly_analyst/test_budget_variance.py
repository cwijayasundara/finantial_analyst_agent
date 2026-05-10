from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import upsert_budget
from cookbooks.monthly_analyst.graph import build_analyst_graph


@pytest.fixture
def ledger(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','a','2025-04-01','2025-04-30','x','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('tesco','Tesco',1)"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) VALUES "
            "('t1','2025-04-10','-100.00','TESCO','tesco',1,'s','a')"
        )
    finally:
        conn.close()
    return tmp_workspace


def test_memo_includes_budget_section(ledger):
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=80.0)
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("memo_page_id") == "memo_2025_04"
    body = (ledger / "wiki" / "memos" / "memo_2025_04.md").read_text()
    assert "## Budget Variance" in body
    assert "[[budget_2025_04_category_groceries]]" in body
    assert "over" in body  # actual 100 vs target 80


def test_memo_omits_budget_section_when_no_budgets(ledger):
    graph = build_analyst_graph()
    graph.invoke({"period": "2025_04"})
    body = (ledger / "wiki" / "memos" / "memo_2025_04.md").read_text()
    # Section header is still present but body says no budgets
    assert "(no budgets set for this period)" in body
