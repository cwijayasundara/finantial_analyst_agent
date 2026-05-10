"""TypedDict shared across analyst graph nodes."""
from __future__ import annotations

from typing import Any, TypedDict

from cookbooks._shared.analytics.anomalies import AnomalyFinding
from cookbooks._shared.analytics.spending import CategorySpend, MerchantSpend


class AnalystState(TypedDict, total=False):
    period: str                          # required input: 'yyyy_mm'
    actor: str                           # default: 'analyst'

    # Populated by load_period
    statements: list[dict[str, Any]]
    transactions_count: int

    # Populated by compute_rollups
    category_totals: list[CategorySpend]
    merchant_totals: list[MerchantSpend]
    account_balance_delta: dict[str, Any]

    # Populated by detect_anomalies
    findings: list[AnomalyFinding]

    # Populated by budget_variance
    budget_variance: list[Any]  # BudgetVariance — Any avoids circular import

    # Populated by draft_memo
    draft_body: str
    draft_citations: list[str]
    draft_cited_values: list[str]

    # Populated by lint_memo
    lint_findings: list[Any]

    # Populated by publish
    memo_page_id: str

    # Populated by report
    report: Any  # AnalystReport — Any to avoid pydantic-in-TypedDict pain

    # Errors and warnings collected by every node
    errors: list[str]
    warnings: list[str]
