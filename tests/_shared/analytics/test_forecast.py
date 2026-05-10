from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.forecast import (
    CategoryForecast, _future_periods, forecast_category,
    holt_smoothing, linear_projection, seasonal_naive,
)
from cookbooks._shared.db import connect_readwrite, init_schema


class TestLinearProjection:
    def test_flat_series_projects_flat(self):
        out, rmse = linear_projection([Decimal("100")] * 5, 3)
        # Flat input → flat projection
        for v in out:
            assert v == Decimal("100.00")
        assert rmse == 0.0

    def test_ascending_series_projects_up(self):
        # 100, 110, 120, 130 → slope ~10
        out, _ = linear_projection(
            [Decimal(str(x)) for x in (100, 110, 120, 130)], 3,
        )
        assert out[0] > Decimal("130")
        assert out[2] > out[0]

    def test_single_value_returns_mean(self):
        out, _ = linear_projection([Decimal("50")], 2)
        assert out == [Decimal("50.00"), Decimal("50.00")]

    def test_empty_returns_zeros(self):
        out, rmse = linear_projection([], 3)
        assert out == [Decimal("0.00")] * 3
        assert rmse == 0.0

    def test_clamps_to_zero(self):
        # Steeply declining series shouldn't project negative spend
        out, _ = linear_projection(
            [Decimal("100"), Decimal("50"), Decimal("0")], 3,
        )
        for v in out:
            assert v >= Decimal("0")


class TestHoltSmoothing:
    def test_flat_series_projects_flat(self):
        out, _ = holt_smoothing([Decimal("100")] * 8, 3)
        for v in out:
            assert abs(v - Decimal("100")) < Decimal("1")

    def test_falls_back_to_linear_for_short_history(self):
        out, _ = holt_smoothing([Decimal("100")], 2)
        assert out == [Decimal("100.00"), Decimal("100.00")]

    def test_trend_is_picked_up(self):
        h = [Decimal(str(x)) for x in (100, 105, 110, 115, 120, 125)]
        out, _ = holt_smoothing(h, 3)
        # Each step should be higher than the previous
        assert out[2] > out[1] > out[0]


class TestSeasonalNaive:
    def test_repeats_prior_year(self):
        # 24 months: ascending 1..24
        h = [Decimal(str(x)) for x in range(1, 25)]
        out, _ = seasonal_naive(h, 3, period=12)
        # Month 25's prediction should equal month 13 (=13)
        assert out[0] == Decimal("13.00")
        assert out[1] == Decimal("14.00")

    def test_falls_back_to_mean_with_short_history(self):
        h = [Decimal("100")] * 10
        out, _ = seasonal_naive(h, 2, period=12)
        for v in out:
            assert v == Decimal("100.00")


class TestFuturePeriods:
    def test_advances_within_year(self):
        assert _future_periods("2025_04", 3) == ["2025_05", "2025_06", "2025_07"]

    def test_crosses_year_boundary(self):
        assert _future_periods("2025_11", 3) == ["2025_12", "2026_01", "2026_02"]


@pytest.fixture
def ledger_with_groceries(tmp_workspace: Path):
    """6 months of groceries spend, slowly trending up."""
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
        # cat_id 1 = groceries
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


class TestForecastCategory:
    def test_returns_typed_forecast(self, ledger_with_groceries):
        out = forecast_category("groceries", "2025_04", horizon=3, lookback=6)
        assert isinstance(out, CategoryForecast)
        assert out.category == "groceries"
        assert len(out.history) == 6
        assert len(out.forecast) == 3
        assert out.forecast_periods == ["2025_05", "2025_06", "2025_07"]

    def test_picks_holt_for_six_months(self, ledger_with_groceries):
        out = forecast_category("groceries", "2025_04", horizon=3, lookback=6)
        assert out.method == "holt_smoothing"

    def test_picks_linear_for_short_history(self, ledger_with_groceries):
        out = forecast_category("groceries", "2025_04", horizon=3, lookback=3)
        # Only 3 months → linear_projection
        assert out.method == "linear_projection"

    def test_monthly_average_computed(self, ledger_with_groceries):
        out = forecast_category("groceries", "2025_04", horizon=3, lookback=6)
        # (100 + 110 + 120 + 125 + 130 + 140) / 6 = 120.83
        assert abs(out.monthly_average - Decimal("120.83")) < Decimal("0.5")

    def test_forecast_trends_upward(self, ledger_with_groceries):
        out = forecast_category("groceries", "2025_04", horizon=3, lookback=6)
        # Series is monotonically increasing → projection should be > average
        assert out.forecast[-1] > out.monthly_average

    def test_missing_months_filled_with_zero(self, ledger_with_groceries):
        # Period ahead of any data → history is all zeros
        out = forecast_category("groceries", "2026_06", horizon=2, lookback=3)
        assert all(v == Decimal("0") for v in out.history)

    def test_unknown_category_returns_empty_history(self, ledger_with_groceries):
        out = forecast_category("does_not_exist", "2025_04", horizon=2, lookback=3)
        assert all(v == Decimal("0") for v in out.history)
