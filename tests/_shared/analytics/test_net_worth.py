from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.net_worth import (
    NetWorthMonthlyDelta, _prev_period, compute_snapshot,
    month_over_month_delta,
)
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import snapshot_net_worth


@pytest.fixture
def two_account_ledger(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES "
            "('savings','Savings','savings'),"
            "('credit','Credit','credit')"
        )
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s_sav','savings','2025-01-01','2025-04-30','sav.pdf','d1','docling'),"
            "('s_crd','credit','2025-01-01','2025-04-30','crd.pdf','d2','docling')"
        )
        # Savings: +£500/mo for 4 months
        for d in ("2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15"):
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?)",
                [f"t_sav_{d}", d, "500.00", "deposit", 5, "s_sav", "savings"],
            )
        # Credit: -£100/mo for 4 months
        for d in ("2025-01-10", "2025-02-10", "2025-03-10", "2025-04-10"):
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?)",
                [f"t_crd_{d}", d, "-100.00", "shop", 8, "s_crd", "credit"],
            )
    finally:
        conn.close()
    return tmp_workspace


def test_compute_snapshot_sums_per_account(two_account_ledger):
    total, by_account = compute_snapshot("2025_04")
    assert by_account["savings"] == Decimal("2000.00")
    assert by_account["credit"] == Decimal("-400.00")
    assert total == Decimal("1600.00")


def test_compute_snapshot_inclusive_of_period_end(two_account_ledger):
    """A snapshot for 2025_01 picks up only January's flows."""
    total, by_account = compute_snapshot("2025_01")
    assert by_account["savings"] == Decimal("500.00")
    assert by_account["credit"] == Decimal("-100.00")
    assert total == Decimal("400.00")


def test_prev_period():
    assert _prev_period("2025_04") == "2025_03"
    assert _prev_period("2025_01") == "2024_12"
    assert _prev_period("2026_07") == "2026_06"


def test_month_over_month_delta_with_prior_snapshot(two_account_ledger):
    snapshot_net_worth(actor="analyst", period="2025_03",
                       total_amount=1200.0,
                       by_account={"savings": 1500.0, "credit": -300.0})
    delta = month_over_month_delta("2025_04")
    assert isinstance(delta, NetWorthMonthlyDelta)
    assert delta.prev_period == "2025_03"
    assert delta.total == Decimal("1600.00")
    assert delta.prev_total == Decimal("1200.00")
    assert delta.delta == Decimal("400.00")
    # pct = 400/1200 ≈ 0.333
    assert 0.33 < (delta.pct_change or 0.0) < 0.34


def test_month_over_month_delta_no_prior_returns_nulls(two_account_ledger):
    delta = month_over_month_delta("2025_04")
    assert delta.prev_period is None
    assert delta.delta is None
    assert delta.pct_change is None
