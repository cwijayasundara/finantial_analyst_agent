"""report node — collapse final state into an AnalystReport."""
from __future__ import annotations

from cookbooks.monthly_analyst.schemas import AnalystReport
from cookbooks.monthly_analyst.state import AnalystState


def report_node(state: AnalystState) -> AnalystState:
    report = AnalystReport(
        period=state.get("period", ""),
        memo_page_id=state.get("memo_page_id"),
        transactions_seen=int(state.get("transactions_count", 0)),
        statements_seen=len(state.get("statements", [])),
        findings_count=len(state.get("findings", [])),
        warnings=list(state.get("warnings", [])),
        errors=list(state.get("errors", [])),
    )
    return {**state, "report": report}
