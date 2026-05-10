"""LangGraph wiring: load → flag → draft → lint → publish → report."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from cookbooks.advisor.nodes.draft_recommendations import draft_recommendations_node
from cookbooks.advisor.nodes.flag_uncertainties import flag_uncertainties_node
from cookbooks.advisor.nodes.lint_recommendations import lint_recommendations_node
from cookbooks.advisor.nodes.load_context import load_context_node
from cookbooks.advisor.nodes.publish_recommendations import publish_recommendations_node
from cookbooks.advisor.nodes.report import report_node
from cookbooks.advisor.state import AdvisorState


def build_advisor_graph():
    g = StateGraph(AdvisorState)
    g.add_node("load_context",          load_context_node)
    g.add_node("flag_uncertainties",    flag_uncertainties_node)
    g.add_node("draft_recommendations", draft_recommendations_node)
    g.add_node("lint_recommendations",  lint_recommendations_node)
    g.add_node("publish_recommendations", publish_recommendations_node)
    g.add_node("report",                report_node)

    g.set_entry_point("load_context")
    g.add_edge("load_context",          "flag_uncertainties")
    g.add_edge("flag_uncertainties",    "draft_recommendations")
    g.add_edge("draft_recommendations", "lint_recommendations")
    g.add_edge("lint_recommendations",  "publish_recommendations")
    g.add_edge("publish_recommendations", "report")
    g.add_edge("report", END)
    return g.compile()
