"""budget_variance node — wraps the analytics helper, populates state."""
from __future__ import annotations

from cookbooks._shared.analytics.budgets import budget_variance
from cookbooks.monthly_analyst.state import AnalystState


def budget_variance_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    variances = budget_variance(period)
    return {**state, "budget_variance": variances}
