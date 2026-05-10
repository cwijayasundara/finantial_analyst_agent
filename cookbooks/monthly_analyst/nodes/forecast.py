"""forecast node — projects next-3-month spend for the top categories."""
from __future__ import annotations

from cookbooks._shared.analytics.forecast import forecast_category
from cookbooks.monthly_analyst.state import AnalystState


def forecast_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    cats = state.get("category_totals", []) or []
    # Top-8 by current month spend keeps the memo readable
    top = sorted(cats, key=lambda c: c.total, reverse=True)[:8]
    forecasts = []
    for c in top:
        try:
            f = forecast_category(c.category, period, horizon=3, lookback=12)
            forecasts.append(f)
        except Exception:
            continue
    return {**state, "forecasts": forecasts}
