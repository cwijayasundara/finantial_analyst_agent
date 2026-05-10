"""publish node — invokes the publish_monthly_memo action."""
from __future__ import annotations

from cookbooks._shared.ontology.functions.actions import invoke_action
from cookbooks.monthly_analyst.state import AnalystState


def publish_node(state: AnalystState) -> AnalystState:
    if state.get("errors"):
        return state  # short-circuit; lint or upstream node already errored

    actor = state.get("actor", "analyst")
    page_id = invoke_action(
        action_id="publish_monthly_memo",
        actor=actor,
        inputs={
            "period": state["period"],
            "body_md": state["draft_body"],
            "citations": state.get("draft_citations", []),
            "confidence": 0.9,
        },
    )
    return {**state, "memo_page_id": page_id}
