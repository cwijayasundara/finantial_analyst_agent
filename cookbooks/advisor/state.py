"""TypedDict shared across advisor graph nodes."""
from __future__ import annotations

from typing import Any, TypedDict


class AdvisorState(TypedDict, total=False):
    period: str                          # required input: 'yyyy_mm'
    actor: str                           # default: 'advisor'

    # Populated by load_context
    memo_body: str
    memo_frontmatter: dict[str, Any]
    budget_variances: list[Any]
    findings: list[Any]
    goal_progress: list[Any]
    low_confidence_merchants: list[dict[str, Any]]
    net_worth_history: list[dict[str, Any]]    # P7
    credit_statements: list[dict[str, Any]]    # P7

    # Populated by flag_uncertainties
    flagged_concepts: list[str]          # page ids of ConceptReview pages

    # Populated by draft_recommendations
    drafts: list[dict[str, Any]]         # each: {"kind","body_md","citations","confidence"}

    # Populated by lint_recommendations
    lint_errors: list[str]

    # Populated by publish_recommendations
    published_ids: list[str]

    # Final
    report: Any
    errors: list[str]
    warnings: list[str]
