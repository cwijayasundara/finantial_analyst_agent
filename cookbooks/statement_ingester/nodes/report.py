"""report node — terminal node that emits an IngestReport."""
from __future__ import annotations

from cookbooks.statement_ingester.schemas import IngestReport
from cookbooks.statement_ingester.state import IngestState


def report_node(state: IngestState) -> IngestState:
    skipped = state.get("skipped_reason") is not None
    rep = IngestReport(
        source_path=state.get("source_path", ""),
        sha256=state.get("sha256", ""),
        parser_used=state.get("parser_used"),
        skipped=skipped,
        skipped_reason=state.get("skipped_reason"),
        new_transactions=len(state.get("new_transactions", [])),
        new_merchants=len(state.get("new_merchants", [])),
        new_subscriptions=len(state.get("recurring_detected", [])),
        completeness_warnings=state.get("completeness_warnings", []),
        errors=state.get("errors", []),
    )
    return {**state, "report": rep}
