"""Integration test for P7 sections (Net Worth + Goals) in monthly memo."""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import upsert_goal
from cookbooks.monthly_analyst.graph import build_analyst_graph


@pytest.fixture
def april_2025_with_goal(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('savings','Sav','savings')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','savings','2025-04-01','2025-04-30','x','d','docling')"
        )
        for d in ("2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15"):
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?)",
                [f"t_{d}", d, "500.00", "deposit", 5, "s", "savings"],
            )
    finally:
        conn.close()
    upsert_goal(
        actor="user", name="holiday-2026", target_amount=8000.0,
        target_date="2026-04-30", scope_type="savings_account",
        scope_id="savings", started_at="2025-01-01",
    )
    return tmp_workspace


def test_memo_includes_net_worth_and_goals_sections(april_2025_with_goal):
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("memo_page_id") == "memo_2025_04"

    s = load_settings()
    body = (s.paths.wiki / "memos" / "memo_2025_04.md").read_text()

    assert "## Net Worth" in body
    assert "Total: £" in body
    assert "[[savings]]" in body

    assert "## Goals progress" in body
    assert "[[goal_holiday_2026_2026-04-30]]" in body
    assert "on_track" in body


def test_memo_lints_clean_with_p7_sections(april_2025_with_goal):
    """All numeric tokens in the new sections must trace to cited_values."""
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    # No memo_lint errors → publish ran → memo file exists
    assert final.get("report").errors == []


def test_net_worth_snapshot_persists(april_2025_with_goal):
    graph = build_analyst_graph()
    graph.invoke({"period": "2025_04"})
    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT total_amount FROM net_worth_snapshots WHERE period='2025_04'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert float(row[0]) == 2000.0  # 4×£500
