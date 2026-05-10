"""advisor report node — collapse final state into AdvisorReport."""
from __future__ import annotations

from cookbooks.advisor.schemas import AdvisorReport
from cookbooks.advisor.state import AdvisorState


def report_node(state: AdvisorState) -> AdvisorState:
    rep = AdvisorReport(
        period=state.get("period", ""),
        published_ids=list(state.get("published_ids", [])),
        flagged_concepts=list(state.get("flagged_concepts", [])),
        errors=list(state.get("lint_errors", [])) + list(state.get("errors", [])),
        warnings=list(state.get("warnings", [])),
    )
    return {**state, "report": rep}
