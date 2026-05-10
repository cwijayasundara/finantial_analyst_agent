"""draft_memo node — template-mode memo body composition.

Default mode is `template` (deterministic). LLM mode (`PFH_MEMO_MODE=llm`)
delegates to the configured chat model with the memo rubric — local-first
ollama by default, opt-in OpenAI via `PFH_ALLOW_REMOTE_LLM=true`.
"""
from __future__ import annotations

import os
from decimal import Decimal

from cookbooks._shared.analytics.spending import period_window
from cookbooks.monthly_analyst.state import AnalystState

_TEMPLATE = """\
# Monthly Memo · {period_human}

## Summary

In {period_human} ({period_start} → {period_end}), the ledger recorded \
{txn_count} transaction(s) across {stmt_count} statement(s).

## Top Categories

{category_lines}

## Top Merchants

{merchant_lines}

## Anomalies

{anomaly_lines}

## Budget Variance

{budget_lines}

## Account Net Flow

{account_lines}
"""


def _human_period(period: str) -> str:
    start, _end = period_window(period)
    return start.strftime("%B %Y")


def _decimal(s: str) -> Decimal:
    return Decimal(str(s))


def draft_memo_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    mode = os.environ.get("PFH_MEMO_MODE", "template").strip().lower()
    if mode == "llm":
        return _draft_via_llm(state)
    return _draft_via_template(state)


def _draft_via_template(state: AnalystState) -> AnalystState:
    period = state["period"]
    start, end = period_window(period)
    cats = state.get("category_totals", [])
    merchs = state.get("merchant_totals", [])
    findings = state.get("findings", [])
    accounts = state.get("account_balance_delta", {})
    statements = state.get("statements", [])

    category_lines = "\n".join(
        f"- {c.category}: £{c.total} ({c.txn_count} txn)"
        for c in cats
    ) or "- (no categorised spend this period)"
    merchant_lines = "\n".join(
        f"- [[merchant_{m.merchant_id}]]: £{m.total} ({m.txn_count} txn)"
        for m in merchs
    ) or "- (no merchant activity)"

    anomaly_lines_list = []
    for f in findings:
        if f.kind == "subscription_drift":
            anomaly_lines_list.append(
                f"- [[sub_{f.subscription_id}]]: drift of "
                f"£{abs(f.actual - f.expected)} on tx {f.transaction_id} "
                f"(expected £{f.expected}, actual £{f.actual})"
            )
        elif f.kind == "merchant_outlier":
            anomaly_lines_list.append(
                f"- [[merchant_{f.merchant_id}]]: £{f.this_month} this month "
                f"vs £{f.monthly_mean} trailing mean (z={f.z_score:.2f})"
            )
    anomaly_lines = "\n".join(anomaly_lines_list) or "- (none)"

    account_lines = "\n".join(
        f"- [[{acct}]]: £{delta}" for acct, delta in accounts.items()
    ) or "- (no movement)"

    variances = state.get("budget_variance", [])
    budget_lines_list = []
    for v in variances:
        flag_glyph = {"over": "⚠", "under": "✓", "on_track": "·"}.get(v.flag, "·")
        budget_lines_list.append(
            f"- {flag_glyph} [[{v.budget_id}]]: actual £{v.actual} vs "
            f"target £{v.target} ({v.flag} by {v.pct:+.1%})"
        )
    budget_lines = "\n".join(budget_lines_list) or "- (no budgets set for this period)"

    body = _TEMPLATE.format(
        period_human=_human_period(period),
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        txn_count=state.get("transactions_count", 0),
        stmt_count=len(statements),
        category_lines=category_lines,
        merchant_lines=merchant_lines,
        anomaly_lines=anomaly_lines,
        budget_lines=budget_lines,
        account_lines=account_lines,
    )

    citations = (
        [s["id"] for s in statements]
        + [f"merchant_{m.merchant_id}" for m in merchs]
    )
    cited_values: list[str] = []
    for c in cats:
        cited_values.append(str(c.total))
    for m in merchs:
        cited_values.append(str(m.total))
    for f in findings:
        if f.kind == "subscription_drift":
            cited_values += [str(f.expected), str(f.actual),
                             str(abs(f.actual - f.expected))]
        elif f.kind == "merchant_outlier":
            cited_values += [str(f.this_month), str(f.monthly_mean)]
    for v in accounts.values():
        cited_values.append(str(v))
    for bv in variances:
        cited_values += [str(bv.target), str(bv.actual), str(bv.delta),
                         f"{bv.pct:.1%}", f"{bv.pct:+.1%}"]

    return {
        **state,
        "draft_body": body,
        "draft_citations": citations,
        "draft_cited_values": cited_values,
    }


def _draft_via_llm(state: AnalystState) -> AnalystState:
    """LLM mode placeholder — calls the configured chat model with the
    memo rubric. Local-first contract preserved.
    """
    from cookbooks._shared.llm import build_chat_model

    chat = build_chat_model()
    template_state = _draft_via_template(state)  # use as the base
    rubric = (
        "Polish the following Markdown memo while preserving every "
        "numeric value, every [[wikilink]], and every section heading. "
        "Tighten prose only. Output ONLY the polished Markdown."
    )
    result = chat.invoke([
        ("system", rubric),
        ("human", template_state["draft_body"]),
    ])
    body = getattr(result, "content", template_state["draft_body"])
    return {**template_state, "draft_body": body}
