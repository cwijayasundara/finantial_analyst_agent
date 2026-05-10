"""load_context — pull memo + budgets + low-confidence merchants for the period."""
from __future__ import annotations

import yaml

from cookbooks._shared.analytics.anomalies import (
    detect_merchant_outliers, detect_subscription_drift,
)
from cookbooks._shared.analytics.budgets import budget_variance
from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks.advisor.state import AdvisorState


def _load_memo(period: str) -> tuple[dict, str]:
    settings = load_settings()
    page = settings.paths.wiki / "memos" / f"memo_{period}.md"
    if not page.exists():
        return {}, ""
    text = page.read_text(encoding="utf-8")
    fm: dict = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            try:
                fm = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                fm = {}
            body = text[end + 5:]
    return fm, body


def load_context_node(state: AdvisorState) -> AdvisorState:
    period = state["period"]
    fm, body = _load_memo(period)

    conn = connect_readonly()
    try:
        # All merchants — flag_uncertainties scans canonical names regardless
        # of whether the merchant currently has transactions.
        rows = conn.execute(
            "SELECT id, canonical_name FROM merchants ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    merchants = [{"id": r[0], "name": r[1]} for r in rows]

    return {
        **state,
        "memo_frontmatter": fm,
        "memo_body": body,
        "budget_variances": budget_variance(period),
        "findings": list(detect_subscription_drift(period))
                  + list(detect_merchant_outliers(period)),
        "low_confidence_merchants": merchants,  # we don't track confidence in DB; return all
    }
