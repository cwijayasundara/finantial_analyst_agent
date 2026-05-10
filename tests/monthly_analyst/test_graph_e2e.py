"""End-to-end test of the analyst StateGraph."""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.monthly_analyst.graph import build_analyst_graph


@pytest.fixture
def april_2025_ledger(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('stmt_2025_04','a','2025-04-01','2025-04-30','x.pdf','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) VALUES "
            "('tesco','Tesco',1),('costa','Costa',3)"
        )
        for tid, dt, amt, mid, cid in [
            ("t1", "2025-04-05", "-25.00", "tesco", 1),
            ("t2", "2025-04-10", "-50.00", "tesco", 1),
            ("t3", "2025-04-20", "-4.50",  "costa", 3),
        ]:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "merchant_id,category_id,statement_id,account_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [tid, dt, amt, "X", mid, cid, "stmt_2025_04", "a"],
            )
    finally:
        conn.close()
    return tmp_workspace


def test_e2e_pipeline_writes_memo(april_2025_ledger):
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("memo_page_id") == "memo_2025_04"
    assert final.get("report").transactions_seen == 3
    assert final.get("report").errors == []

    s = load_settings()
    page = s.paths.wiki / "memos" / "memo_2025_04.md"
    assert page.exists()
    body = page.read_text()
    assert "[[merchant_tesco]]" in body
    assert "April 2025" in body


def test_e2e_empty_period_still_writes_memo(tmp_workspace):
    """A period with no transactions still produces a memo with empty sections."""
    from cookbooks._shared.db import init_schema
    init_schema()

    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("memo_page_id") == "memo_2025_04"
    s = load_settings()
    body = (s.paths.wiki / "memos" / "memo_2025_04.md").read_text()
    assert "April 2025" in body
    assert "(no categorised spend this period)" in body
