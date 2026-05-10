"""lint_recommendations — every numeric token in a recommendation must trace
to a cited rollup or finding (reuses memo_lint primitive)."""
from __future__ import annotations

from cookbooks._shared.analytics.memo_lint import (
    MemoCompletenessError, lint_memo,
)
from cookbooks.advisor.state import AdvisorState


def lint_recommendations_node(state: AdvisorState) -> AdvisorState:
    errors: list[str] = []
    for d in state.get("drafts", []):
        try:
            lint_memo(
                d["body_md"], cited_values=set(d.get("cited_values", [])),
                hard_fail=True,
            )
        except MemoCompletenessError as exc:
            errors.append(f"{d['kind']}: {exc}")
    return {**state, "lint_errors": errors}
