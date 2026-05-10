"""draft_recommendations — template-mode drafts derived from variance + findings."""
from __future__ import annotations

from decimal import Decimal

from cookbooks._shared.analytics.debt import (
    is_infinite, payoff_horizon, recommended_payment, total_interest,
)
from cookbooks.advisor.state import AdvisorState


def draft_recommendations_node(state: AdvisorState) -> AdvisorState:
    drafts: list[dict] = []
    period = state["period"]

    # subscription_cancel: any subscription drift > 50%
    for f in state.get("findings", []):
        if getattr(f, "kind", "") != "subscription_drift":
            continue
        if f.delta_pct < 0.5:
            continue
        body = (
            f"## Consider reviewing subscription [[sub_{f.subscription_id}]]\n\n"
            f"In {period}, this subscription charged £{f.actual} versus the "
            f"expected £{f.expected} (drift of {f.delta_pct:+.1%}). If the "
            "service is no longer used, consider cancelling."
        )
        drafts.append({
            "kind": "subscription_cancel",
            "body_md": body,
            "citations": [f"sub_{f.subscription_id}"],
            "cited_values": [str(f.actual), str(f.expected),
                             f"{f.delta_pct:+.1%}", f"{f.delta_pct:.1%}"],
            "confidence": 0.7,
        })

    # budget_adjust: over-budget by ≥ 20%
    for v in state.get("budget_variances", []):
        if v.flag != "over" or v.pct < 0.20:
            continue
        body = (
            f"## Reconsider budget [[{v.budget_id}]]\n\n"
            f"In {period}, actual spend was £{v.actual} versus the target "
            f"£{v.target} ({v.pct:+.1%}). Either tighten this category's "
            f"spending or raise the budget to reflect new norms."
        )
        drafts.append({
            "kind": "budget_adjust",
            "body_md": body,
            "citations": [v.budget_id],
            "cited_values": [str(v.actual), str(v.target),
                             f"{v.pct:+.1%}", f"{v.pct:.1%}"],
            "confidence": 0.65,
        })

    # goal_off_track: any active goal where on_track is False
    for g in state.get("goal_progress", []):
        if getattr(g, "on_track", True):
            continue
        body = (
            f"## You're behind on goal [[{g.goal_id}]]\n\n"
            f"After {g.months_elapsed} of {g.months_total} months, "
            f"progress on **{g.name}** is £{g.current_amount} versus a "
            f"£{g.target_amount} target by {g.target_date}. "
            f"To finish on time, you'd need to put aside £{g.monthly_required} "
            f"per month for the remaining {max(g.months_total - g.months_elapsed, 1)} "
            "month(s)."
        )
        drafts.append({
            "kind": "goal_off_track",
            "body_md": body,
            "citations": [g.goal_id],
            "cited_values": [
                str(g.current_amount), str(g.target_amount),
                str(g.monthly_required),
                str(g.months_elapsed), str(g.months_total),
                str(max(g.months_total - g.months_elapsed, 1)),
            ],
            "confidence": 0.7,
        })

    # anomaly_investigate: merchant_outlier with z > 3
    for f in state.get("findings", []):
        if getattr(f, "kind", "") != "merchant_outlier":
            continue
        if abs(getattr(f, "z_score", 0.0)) < 3.0:
            continue
        body = (
            f"## Investigate spend at [[merchant_{f.merchant_id}]]\n\n"
            f"In {period}, you spent £{f.this_month} at this merchant — "
            f"{f.z_score:+.2f}σ from the trailing average of £{f.monthly_mean}. "
            "Look for a one-off purchase, a missed cancellation, or a "
            "categorisation error."
        )
        drafts.append({
            "kind": "anomaly_investigate",
            "body_md": body,
            "citations": [f"merchant_{f.merchant_id}"],
            "cited_values": [str(f.this_month), str(f.monthly_mean),
                             f"{f.z_score:+.2f}", f"{f.z_score:.2f}"],
            "confidence": 0.6,
        })

    # P7 — credit_payoff_accelerate: high-APR balance with minimum-pay drag
    for c in state.get("credit_statements", []):
        outstanding = c.get("outstanding")
        apr = c.get("apr")
        min_pay = c.get("min_payment")
        if not (outstanding and apr and min_pay):
            continue
        if apr < Decimal("0.10"):
            continue  # not high-APR
        horizon_min = payoff_horizon(float(outstanding), float(apr), float(min_pay))
        if is_infinite(horizon_min) or horizon_min < 24:
            continue
        # Suggest a payment that clears in half the current horizon
        target_months = max(horizon_min // 2, 12)
        suggested = recommended_payment(
            float(outstanding), float(apr), int(target_months),
        )
        interest_min = total_interest(float(outstanding), float(apr), float(min_pay))
        interest_suggested = total_interest(
            float(outstanding), float(apr), float(suggested),
        )
        body = (
            f"## Pay down [[{c['account_id']}]] faster\n\n"
            f"Outstanding £{outstanding} at {float(apr) * 100:.1f}% APR. At "
            f"the £{min_pay}/month minimum, payoff takes {horizon_min} months "
            f"with £{interest_min} in interest. Paying £{suggested}/month "
            f"clears it in {target_months} months and cuts interest to "
            f"£{interest_suggested}."
        )
        drafts.append({
            "kind": "credit_payoff_accelerate",
            "body_md": body,
            "citations": [c["account_id"], c["statement_id"]],
            "cited_values": [
                str(outstanding), str(min_pay), str(suggested),
                str(interest_min), str(interest_suggested),
                str(horizon_min), str(target_months),
                f"{float(apr) * 100:.1f}%",
            ],
            "confidence": 0.7,
        })

    # P7 — net_worth_decline: total fell for two consecutive months
    hist = state.get("net_worth_history", []) or []
    if len(hist) >= 3:
        # hist is sorted period DESC: index 0 = current, 1 = prev, 2 = prev2
        d_now = hist[0]["total"] - hist[1]["total"]
        d_prev = hist[1]["total"] - hist[2]["total"]
        if d_now < 0 and d_prev < 0:
            body = (
                f"## Net worth has fallen for two consecutive months\n\n"
                f"In {hist[0]['period']}, total was £{hist[0]['total']} "
                f"(down £{abs(d_now)} from the previous month). The month "
                f"before that, it fell by £{abs(d_prev)}. Review the "
                f"`Top Categories` section of [[memo_{period}]] to find "
                f"what's driving the drawdown."
            )
            drafts.append({
                "kind": "net_worth_decline",
                "body_md": body,
                "citations": [f"snap_{hist[0]['period']}",
                              f"snap_{hist[1]['period']}",
                              f"snap_{hist[2]['period']}"],
                "cited_values": [
                    str(hist[0]["total"]), str(hist[1]["total"]),
                    str(hist[2]["total"]),
                    str(abs(d_now)), str(abs(d_prev)),
                ],
                "confidence": 0.55,
            })

    return {**state, "drafts": drafts}
