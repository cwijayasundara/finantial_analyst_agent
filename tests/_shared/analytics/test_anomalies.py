from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.anomalies import (
    AnomalyFinding,
    detect_merchant_outliers,
    detect_subscription_drift,
)
from cookbooks._shared.db import connect_readwrite, init_schema


@pytest.fixture
def ledger_with_subscription_drift(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','a','2025-04-01','2025-04-30','x.pdf','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('netflix','Netflix',4)"
        )
        conn.execute(
            "INSERT INTO patterns(id,merchant_id,cadence,expected_amount,"
            "last_seen,confidence) VALUES ('netflix','netflix','monthly',11.99,"
            "'2025-04-05',0.95)"
        )
        # Two transactions: one within tolerance, one drifting +25%
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id,pattern_id) VALUES "
            "('t_ok','2025-04-05','-11.99','NETFLIX','netflix',4,'s','a','netflix'),"
            "('t_drift','2025-04-05','-14.99','NETFLIX','netflix',4,'s','a','netflix')"
        )
    finally:
        conn.close()
    return tmp_workspace


@pytest.fixture
def ledger_with_merchant_outlier(tmp_workspace: Path):
    """6 prior months at ~£100, one month at £500 — clear z-score outlier."""
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('groc','Groc',1)"
        )
        for i, period in enumerate([
            ("2024-10", "2024-10-15"), ("2024-11", "2024-11-15"),
            ("2024-12", "2024-12-15"), ("2025-01", "2025-01-15"),
            ("2025-02", "2025-02-15"), ("2025-03", "2025-03-15"),
        ]):
            ym, dt = period
            conn.execute(
                "INSERT INTO statements(id,account_id,period_start,period_end,"
                "source_pdf,sha256,parser_used) VALUES "
                "(?,?,?,?,?,?,?)",
                [f"s_{ym}", "a", f"{ym}-01", f"{ym}-28",
                 f"{ym}.pdf", f"h{i}", "docling"],
            )
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?,?)",
                [f"t_{ym}", dt, "-100.00", "GROC", "groc", 1,
                 f"s_{ym}", "a"],
            )
        # Outlier month
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s_2025-04','a','2025-04-01','2025-04-30','apr.pdf','outl','docling')"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) VALUES "
            "('t_outlier','2025-04-15','-500.00','GROC','groc',1,'s_2025-04','a')"
        )
    finally:
        conn.close()
    return tmp_workspace


class TestSubscriptionDrift:
    def test_flags_drifting_transaction(self, ledger_with_subscription_drift):
        findings = detect_subscription_drift("2025_04")
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "subscription_drift"
        assert f.subscription_id == "netflix"
        assert f.transaction_id == "t_drift"
        assert f.expected == Decimal("11.99")
        assert f.actual == Decimal("14.99")
        # delta = (14.99 - 11.99) / 11.99 ≈ 0.25
        assert 0.24 < f.delta_pct < 0.26

    def test_within_tolerance_is_not_flagged(self, ledger_with_subscription_drift):
        findings = detect_subscription_drift("2025_04", tolerance=0.30)
        # Both transactions within 30% — neither flagged
        assert findings == []

    def test_env_var_overrides_default(self, ledger_with_subscription_drift, monkeypatch):
        monkeypatch.setenv("PFH_SUB_DEV_TOL", "0.30")
        findings = detect_subscription_drift("2025_04")
        assert findings == []


class TestMerchantOutlier:
    def test_flags_z_score_outlier(self, ledger_with_merchant_outlier):
        findings = detect_merchant_outliers("2025_04", lookback_months=6)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "merchant_outlier"
        assert f.merchant_id == "groc"
        assert f.this_month == Decimal("500.00")
        assert f.monthly_mean == Decimal("100.00")
        assert f.z_score > 2.0  # 5σ given 0 stdev — capped or huge

    def test_no_outlier_when_history_too_short(self, tmp_workspace):
        init_schema()
        # Empty ledger → no findings
        assert detect_merchant_outliers("2025_04", lookback_months=6) == []
