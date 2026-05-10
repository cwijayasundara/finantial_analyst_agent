"""Integration: forecast node + memo Forecast section."""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.monthly_analyst.graph import build_analyst_graph


@pytest.fixture
def ledger_with_history(tmp_workspace: Path):
    """6 months of groceries spend feeding into the April 2025 memo."""
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','a','2024-11-01','2025-04-30','x','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('tesco','Tesco',1)"
        )
        # Ascending grocery spend so the forecast is non-flat
        for ym, amt in [
            ("2024-11", 100), ("2024-12", 110), ("2025-01", 120),
            ("2025-02", 125), ("2025-03", 130), ("2025-04", 140),
        ]:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "merchant_id,category_id,statement_id,account_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [f"t_{ym}", f"{ym}-15", str(-amt), "TESCO", "tesco", 1, "s", "a"],
            )
    finally:
        conn.close()
    return tmp_workspace


def test_memo_includes_forecast_section(ledger_with_history):
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("memo_page_id") == "memo_2025_04"

    s = load_settings()
    body = (s.paths.wiki / "memos" / "memo_2025_04.md").read_text()

    assert "## Forecast (next 3 months)" in body
    assert "groceries" in body
    assert "holt_smoothing" in body or "linear_projection" in body


def test_memo_lints_clean_with_forecast(ledger_with_history):
    graph = build_analyst_graph()
    final = graph.invoke({"period": "2025_04"})
    assert final.get("report").errors == []


def test_forecast_state_populated(ledger_with_history):
    from cookbooks.monthly_analyst.nodes.forecast import forecast_node
    from cookbooks._shared.analytics.spending import category_totals

    state = {
        "period": "2025_04",
        "category_totals": list(category_totals("2025_04")),
    }
    out = forecast_node(state)
    assert "forecasts" in out
    forecasts = out["forecasts"]
    assert len(forecasts) >= 1
    g = next((f for f in forecasts if f.category == "groceries"), None)
    assert g is not None
    assert len(g.forecast) == 3
