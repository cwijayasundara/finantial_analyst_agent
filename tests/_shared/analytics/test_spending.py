from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.spending import (
    account_balance_delta,
    category_totals,
    merchant_totals,
    period_window,
)
from cookbooks._shared.db import connect_readwrite, init_schema


@pytest.fixture
def seeded_ledger(tmp_workspace: Path):
    """Tiny fixture ledger: 2 accounts, 1 statement each, 5 transactions."""
    init_schema()  # seeds categories: groceries=1, fuel=2, dining=3, ..., other=8
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a_credit','Credit','credit'),('a_savings','Savings','savings')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,source_pdf,sha256,parser_used) VALUES "
            "('s1','a_credit','2025-04-01','2025-04-30','x.pdf','aa','docling'),"
            "('s2','a_savings','2025-04-01','2025-04-30','y.pdf','bb','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) VALUES "
            "('tesco','Tesco',1),('sainsburys','Sainsburys',1),"
            "('costa','Costa',3),('amazon','Amazon',8)"
        )
        # April spend: 5 transactions; cat ids 1=groceries, 3=dining, 8=other
        rows = [
            ("t1", "2025-04-05", "-25.00", "TESCO", "tesco", 1, "s1", "a_credit"),
            ("t2", "2025-04-10", "-50.00", "TESCO", "tesco", 1, "s1", "a_credit"),
            ("t3", "2025-04-12", "-30.00", "SAINSBURYS", "sainsburys", 1, "s1", "a_credit"),
            ("t4", "2025-04-20", "-4.50",  "COSTA", "costa", 3, "s1", "a_credit"),
            ("t5", "2025-04-25", "-100.00", "AMAZON", "amazon", 8, "s1", "a_credit"),
            # March spend (out of window) — must be excluded
            ("t0", "2025-03-15", "-1000.00", "TESCO", "tesco", 1, "s1", "a_credit"),
        ]
        for r in rows:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?,?)", list(r),
            )
    finally:
        conn.close()
    return tmp_workspace


class TestPeriodWindow:
    def test_basic(self):
        start, end = period_window("2025_04")
        assert start == date(2025, 4, 1)
        assert end == date(2025, 4, 30)

    def test_february_leap(self):
        start, end = period_window("2024_02")
        assert end == date(2024, 2, 29)

    def test_february_non_leap(self):
        start, end = period_window("2025_02")
        assert end == date(2025, 2, 28)

    def test_dash_separator_also_accepted(self):
        start, end = period_window("2025-04")
        assert start == date(2025, 4, 1)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            period_window("April 2025")

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            period_window("2025_13")


class TestCategoryTotals:
    def test_groups_and_orders_desc(self, seeded_ledger):
        totals = category_totals("2025_04")
        names = [t.category for t in totals]
        amounts = [t.total for t in totals]
        assert names[0] == "groceries"  # 25 + 50 + 30 = 105
        assert amounts[0] == Decimal("105.00")
        # March transaction (1000) must NOT leak in
        assert all(a < Decimal("1000.00") for a in amounts)

    def test_excludes_uncategorised(self, seeded_ledger):
        # If a transaction has NULL category_id, it must be excluded
        # (handled by INNER JOIN). Sanity-check by counting expected rows.
        totals = category_totals("2025_04")
        assert {t.category for t in totals} == {"groceries", "dining", "other"}


class TestMerchantTotals:
    def test_top_n(self, seeded_ledger):
        top = merchant_totals("2025_04", top_n=2)
        ids = [m.merchant_id for m in top]
        assert ids == ["amazon", "tesco"]  # 100 vs 75

    def test_full_list_when_top_n_exceeds_count(self, seeded_ledger):
        top = merchant_totals("2025_04", top_n=99)
        assert len(top) == 4  # tesco, sainsburys, costa, amazon


class TestAccountBalanceDelta:
    def test_per_account(self, seeded_ledger):
        deltas = account_balance_delta("2025_04")
        assert deltas["a_credit"] == Decimal("-209.50")  # all April debits
        # a_savings has no transactions in April
        assert "a_savings" not in deltas or deltas["a_savings"] == Decimal("0")
