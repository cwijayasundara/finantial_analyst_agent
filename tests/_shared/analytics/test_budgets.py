from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.budgets import budget_variance
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    upsert_budget,
    upsert_merchant,
)


@pytest.fixture
def ledger_with_april_spend(tmp_workspace: Path):
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
            "INSERT INTO merchants(id,canonical_name,category_id) VALUES "
            "('tesco','Tesco',1),('costa','Costa',3)"
        )
        # 100 in groceries, 50 in dining
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) VALUES "
            "('t1','2025-04-05','-100.00','TESCO','tesco',1,'s','a'),"
            "('t2','2025-04-10','-50.00','COSTA','costa',3,'s','a')"
        )
    finally:
        conn.close()
    return tmp_workspace


def test_no_budgets_returns_empty(ledger_with_april_spend):
    assert budget_variance("2025_04") == []


def test_under_budget(ledger_with_april_spend):
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=200.0)
    [v] = budget_variance("2025_04")
    assert v.target == Decimal("200.00")
    assert v.actual == Decimal("100.00")
    assert v.delta == Decimal("-100.00")
    assert v.flag == "under"


def test_over_budget(ledger_with_april_spend):
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=80.0)
    [v] = budget_variance("2025_04")
    assert v.flag == "over"
    assert v.delta == Decimal("20.00")


def test_on_track_within_tolerance(ledger_with_april_spend):
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=100.0)
    [v] = budget_variance("2025_04")
    assert v.flag == "on_track"


def test_annual_budget_spreads_monthly(ledger_with_april_spend):
    # £1200 annual → £100/month → exactly meets the £100 actual
    upsert_budget(actor="analyst", period="annual:2025",
                  scope_type="category", scope_id="groceries",
                  target_amount=1200.0)
    [v] = budget_variance("2025_04")
    assert v.target == Decimal("100.00")
    assert v.flag == "on_track"


def test_merchant_scope(ledger_with_april_spend):
    upsert_merchant(actor="ingester", merchant_id="tesco",
                    canonical_name="Tesco", category="groceries", aliases=[])
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="merchant", scope_id="tesco",
                  target_amount=50.0)
    out = [v for v in budget_variance("2025_04") if v.scope_type == "merchant"]
    assert len(out) == 1
    assert out[0].scope_id == "tesco"
    assert out[0].actual == Decimal("100.00")
    assert out[0].flag == "over"


def test_tolerance_env_var(ledger_with_april_spend, monkeypatch):
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=80.0)  # actual 100, +25%
    monkeypatch.setenv("PFH_BUDGET_TOLERANCE", "0.30")
    [v] = budget_variance("2025_04")
    assert v.flag == "on_track"  # within 30% tolerance now
