"""lint_memo node — wraps the memo_lint primitive."""
from __future__ import annotations

from cookbooks._shared.analytics.memo_lint import (
    MemoCompletenessError,
    lint_memo,
)
from cookbooks.monthly_analyst.state import AnalystState


def lint_memo_node(state: AnalystState) -> AnalystState:
    body = state.get("draft_body", "")
    cited_values = set(state.get("draft_cited_values", []))
    try:
        findings = lint_memo(body, cited_values=cited_values, hard_fail=True)
    except MemoCompletenessError as exc:
        errors = list(state.get("errors", []))
        errors.append(f"memo_lint: {exc}")
        return {
            **state,
            "errors": errors,
            "lint_findings": [],
        }
    return {**state, "lint_findings": findings}
