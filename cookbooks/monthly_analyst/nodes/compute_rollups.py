"""compute_rollups node — wraps the spending analytics primitives."""
from __future__ import annotations

from cookbooks._shared.analytics.spending import (
    account_balance_delta,
    category_totals,
    merchant_totals,
)
from cookbooks.monthly_analyst.state import AnalystState


def compute_rollups_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    return {
        **state,
        "category_totals": category_totals(period),
        "merchant_totals": merchant_totals(period, top_n=10),
        "account_balance_delta": {
            k: str(v) for k, v in account_balance_delta(period).items()
        },
    }
