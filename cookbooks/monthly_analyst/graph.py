"""LangGraph StateGraph wiring for the monthly-analyst pipeline.

load_period → compute_rollups → compute_net_worth → compute_goals
            → budget_variance → forecast → detect_anomalies → draft_memo
            → lint_memo → publish → report
                       ↘ (errors) ↗
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from cookbooks.monthly_analyst.nodes.budget_variance import budget_variance_node
from cookbooks.monthly_analyst.nodes.compute_goals import compute_goals_node
from cookbooks.monthly_analyst.nodes.compute_net_worth import compute_net_worth_node
from cookbooks.monthly_analyst.nodes.compute_rollups import compute_rollups_node
from cookbooks.monthly_analyst.nodes.detect_anomalies import detect_anomalies_node
from cookbooks.monthly_analyst.nodes.draft_memo import draft_memo_node
from cookbooks.monthly_analyst.nodes.forecast import forecast_node
from cookbooks.monthly_analyst.nodes.lint_memo import lint_memo_node
from cookbooks.monthly_analyst.nodes.load_period import load_period_node
from cookbooks.monthly_analyst.nodes.publish import publish_node
from cookbooks.monthly_analyst.nodes.report import report_node
from cookbooks.monthly_analyst.state import AnalystState


def _route_after_lint(state: AnalystState) -> str:
    if state.get("errors"):
        return "report"
    return "publish"


def build_analyst_graph():
    g = StateGraph(AnalystState)
    g.add_node("load_period",       load_period_node)
    g.add_node("compute_rollups",   compute_rollups_node)
    g.add_node("compute_net_worth", compute_net_worth_node)
    g.add_node("compute_goals",     compute_goals_node)
    g.add_node("budget_variance",   budget_variance_node)
    g.add_node("forecast",          forecast_node)
    g.add_node("detect_anomalies",  detect_anomalies_node)
    g.add_node("draft_memo",        draft_memo_node)
    g.add_node("lint_memo",         lint_memo_node)
    g.add_node("publish",           publish_node)
    g.add_node("report",            report_node)

    g.set_entry_point("load_period")
    g.add_edge("load_period",       "compute_rollups")
    g.add_edge("compute_rollups",   "compute_net_worth")
    g.add_edge("compute_net_worth", "compute_goals")
    g.add_edge("compute_goals",     "budget_variance")
    g.add_edge("budget_variance",   "forecast")
    g.add_edge("forecast",          "detect_anomalies")
    g.add_edge("detect_anomalies",  "draft_memo")
    g.add_edge("draft_memo",        "lint_memo")
    g.add_conditional_edges(
        "lint_memo",
        _route_after_lint,
        {"publish": "publish", "report": "report"},
    )
    g.add_edge("publish",           "report")
    g.add_edge("report",            END)
    return g.compile()
