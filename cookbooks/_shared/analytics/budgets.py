"""Budget variance helpers.

Reads the `budgets` table + reuses `spending.py` to compute actual spend.
Returns one `BudgetVariance` per budget that applies to the period
(monthly or annual-spread-monthly).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from cookbooks._shared.analytics.spending import (
    category_totals, merchant_totals, period_window,
)
from cookbooks._shared.db import connect_readonly

_TOL_DEFAULT = 0.05


@dataclass(frozen=True)
class BudgetVariance:
    budget_id: str
    period: str
    scope_type: Literal["category", "merchant"]
    scope_id: str
    target: Decimal
    actual: Decimal
    delta: Decimal           # actual - target (positive = over)
    pct: float               # delta / target
    flag: Literal["over", "under", "on_track"]


def _resolve_tolerance() -> float:
    raw = os.environ.get("PFH_BUDGET_TOLERANCE", "").strip()
    if not raw:
        return _TOL_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _TOL_DEFAULT


def _annual_to_monthly(period: str, target: Decimal) -> Decimal:
    """Annual targets get spread evenly across 12 months."""
    return (target / Decimal(12)).quantize(Decimal("0.01"))


def budget_variance(period: str) -> list[BudgetVariance]:
    """Variance for every Budget that applies to `period` (yyyy_mm)."""
    period_window(period)  # validate format
    annual_key = f"annual:{period[:4]}"
    tol = _resolve_tolerance()

    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT id, period, scope_type, scope_id, "
            "CAST(target_amount AS VARCHAR) "
            "FROM budgets WHERE period IN (?, ?)",
            [period, annual_key],
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    actual_by_cat = {c.category: c.total for c in category_totals(period)}
    actual_by_merchant = {
        m.merchant_id: m.total for m in merchant_totals(period, top_n=10_000)
    }

    out: list[BudgetVariance] = []
    for budget_id, b_period, scope_type, scope_id, target_s in rows:
        target = Decimal(target_s)
        if b_period == annual_key:
            target = _annual_to_monthly(period, target)

        if scope_type == "category":
            actual = actual_by_cat.get(scope_id, Decimal("0"))
        else:  # merchant
            actual = actual_by_merchant.get(scope_id, Decimal("0"))

        delta = actual - target
        if target == 0:
            pct = float("inf") if delta != 0 else 0.0
        else:
            pct = float(delta / target)
        if abs(pct) <= tol:
            flag = "on_track"
        elif pct > 0:
            flag = "over"
        else:
            flag = "under"
        out.append(BudgetVariance(
            budget_id=budget_id, period=period,
            scope_type=scope_type, scope_id=scope_id,
            target=target, actual=actual, delta=delta, pct=pct, flag=flag,
        ))
    return out
