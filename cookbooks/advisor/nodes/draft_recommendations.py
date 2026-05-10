"""draft_recommendations — template-mode drafts derived from variance + findings."""
from __future__ import annotations

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

    return {**state, "drafts": drafts}
