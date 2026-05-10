"""compute_net_worth node — computes total + by-account position
at period end and persists via snapshot_net_worth action.
"""
from __future__ import annotations

from cookbooks._shared.analytics.net_worth import (
    compute_snapshot, month_over_month_delta,
)
from cookbooks._shared.ontology.functions.actions import snapshot_net_worth
from cookbooks.monthly_analyst.state import AnalystState


def compute_net_worth_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    total, by_account = compute_snapshot(period)
    snapshot_net_worth(
        actor=state.get("actor", "analyst"),
        period=period,
        total_amount=float(total),
        by_account={k: float(v) for k, v in by_account.items()},
    )
    delta = month_over_month_delta(period)
    return {
        **state,
        "net_worth_total": total,
        "net_worth_by_account": by_account,
        "net_worth_delta": delta,
    }
