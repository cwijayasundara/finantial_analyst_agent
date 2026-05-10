from __future__ import annotations

from decimal import Decimal

import pytest

from cookbooks._shared.analytics.debt import (
    amortisation, is_infinite, payoff_horizon, recommended_payment,
    total_interest,
)


class TestPayoffHorizon:
    def test_zero_apr_pays_off_linearly(self):
        # £1200 at 0% APR with £100/mo → 12 months
        assert payoff_horizon(1200, 0.0, 100) == 12

    def test_high_apr_typical_credit_card(self):
        # £2400 at 19.9% APR with £75/mo (min payment) — multi-year
        horizon = payoff_horizon(2400, 0.199, 75)
        assert 40 < horizon < 60

    def test_payment_below_interest_returns_sentinel(self):
        # £10 000 at 24% APR with £100/mo — monthly interest is £200,
        # so balance grows forever.
        h = payoff_horizon(10_000, 0.24, 100)
        assert is_infinite(h)

    def test_amortisation_pays_balance_down(self):
        sched = amortisation(1200, 0.0, 100)
        assert sched[-1].balance == Decimal("0.00")
        assert len(sched) == 12

    def test_amortisation_empty_when_zero_balance(self):
        assert amortisation(0, 0.10, 100) == []


class TestTotalInterest:
    def test_zero_apr_zero_interest(self):
        assert total_interest(1200, 0.0, 100) == Decimal("0")

    def test_high_apr_significant_interest(self):
        # £2400 / 19.9% / £75 min → hundreds of pounds in interest
        interest = total_interest(2400, 0.199, 75)
        assert interest > Decimal("500")
        assert interest < Decimal("2000")


class TestRecommendedPayment:
    def test_zero_apr_is_linear(self):
        # £1200 over 12 months at 0% → £100/mo
        assert recommended_payment(1200, 0.0, 12) == Decimal("100.00")

    def test_apr_pushes_payment_up(self):
        # Same £1200 at 19.9% over 12 months requires more than £100
        p = recommended_payment(1200, 0.199, 12)
        assert p > Decimal("100")
        assert p < Decimal("130")

    def test_rejects_non_positive_horizon(self):
        with pytest.raises(ValueError):
            recommended_payment(1000, 0.10, 0)
