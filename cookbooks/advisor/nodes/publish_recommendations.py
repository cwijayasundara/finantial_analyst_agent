"""publish_recommendations — invoke publish_recommendation per draft (skip
if any lint errors collected upstream)."""
from __future__ import annotations

from cookbooks._shared.ontology.functions.actions import invoke_action
from cookbooks.advisor.state import AdvisorState


def publish_recommendations_node(state: AdvisorState) -> AdvisorState:
    if state.get("lint_errors"):
        return {**state, "published_ids": []}

    actor = state.get("actor", "advisor")
    period = state["period"]
    published: list[str] = []
    for d in state.get("drafts", []):
        page = invoke_action(
            action_id="publish_recommendation",
            actor=actor,
            inputs={
                "period": period,
                "kind": d["kind"],
                "body_md": d["body_md"],
                "citations": d.get("citations", []),
                "confidence": d.get("confidence", 0.7),
            },
        )
        published.append(page)
    return {**state, "published_ids": published}
