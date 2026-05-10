"""compute_goals node — scores every active Goal for the period."""
from __future__ import annotations

from cookbooks._shared.analytics.goals import all_active_goals_progress
from cookbooks.monthly_analyst.state import AnalystState


def compute_goals_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    return {**state, "goal_progress": all_active_goals_progress(period)}
