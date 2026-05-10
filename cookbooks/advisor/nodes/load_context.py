"""load_context — pull memo + budgets + low-confidence merchants +
goal progress + recent net-worth snapshots for the period."""
from __future__ import annotations

import json
from decimal import Decimal

import yaml

from cookbooks._shared.analytics.anomalies import (
    detect_merchant_outliers, detect_subscription_drift,
)
from cookbooks._shared.analytics.budgets import budget_variance
from cookbooks._shared.analytics.goals import all_active_goals_progress
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


def _load_recent_snapshots(period: str, limit: int = 3) -> list[dict]:
    """Return up to `limit` most-recent snapshots at or before `period`."""
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT period, CAST(total_amount AS VARCHAR), by_account "
            "FROM net_worth_snapshots "
            "WHERE period <= ? ORDER BY period DESC LIMIT ?",
            [period, int(limit)],
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for p, total, by_account in rows:
        try:
            ba = json.loads(by_account) if by_account else {}
        except Exception:
            ba = {}
        out.append({
            "period": p,
            "total": Decimal(total),
            "by_account": ba,
        })
    return out


def _load_credit_statements(period: str) -> list[dict]:
    """Most-recent credit statements per account with APR + outstanding set."""
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT s.id, s.account_id, "
            "       CAST(s.outstanding_balance AS VARCHAR), "
            "       CAST(s.apr AS VARCHAR), "
            "       CAST(s.min_payment AS VARCHAR) "
            "FROM statements s "
            "JOIN accounts a ON a.id = s.account_id "
            "WHERE a.type = 'credit' "
            "  AND s.outstanding_balance IS NOT NULL "
            "  AND s.apr IS NOT NULL "
            "  AND s.period_end <= ? "
            "ORDER BY s.account_id, s.period_end DESC"
        , [f"{period[:4]}-{period[5:7]}-31"]).fetchall()
    finally:
        conn.close()
    # Keep only the most-recent row per account
    seen: set[str] = set()
    out: list[dict] = []
    for sid, account_id, outstanding, apr, min_pay in rows:
        if account_id in seen:
            continue
        seen.add(account_id)
        out.append({
            "statement_id": sid,
            "account_id": account_id,
            "outstanding": Decimal(outstanding) if outstanding else None,
            "apr": Decimal(apr) if apr else None,
            "min_payment": Decimal(min_pay) if min_pay else None,
        })
    return out


def load_context_node(state: AdvisorState) -> AdvisorState:
    period = state["period"]
    fm, body = _load_memo(period)

    conn = connect_readonly()
    try:
        merchants_rows = conn.execute(
            "SELECT id, canonical_name FROM merchants ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    merchants = [{"id": r[0], "name": r[1]} for r in merchants_rows]

    try:
        goals = all_active_goals_progress(period)
    except Exception:
        goals = []

    return {
        **state,
        "memo_frontmatter": fm,
        "memo_body": body,
        "budget_variances": budget_variance(period),
        "findings": list(detect_subscription_drift(period))
                  + list(detect_merchant_outliers(period)),
        "low_confidence_merchants": merchants,
        "goal_progress": goals,
        "net_worth_history": _load_recent_snapshots(period, limit=3),
        "credit_statements": _load_credit_statements(period),
    }
