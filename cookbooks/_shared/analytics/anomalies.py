"""Anomaly detection over the DuckDB ledger.

Two finding kinds:
- `subscription_drift` — transaction amount diverges from the
  Subscription's `expected_amount` by more than `tolerance` (default 5%).
  Threshold overridable via `PFH_SUB_DEV_TOL` env var.
- `merchant_outlier` — a merchant's spend in the target month is more
  than `z_threshold` standard deviations from its trailing-N-month mean.
  Threshold overridable via `PFH_OUTLIER_Z` env var (default 2.0).

Pure read functions; no LLM, no writes. Returned findings are consumed
by the monthly-analyst's `detect_anomalies` node.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from cookbooks._shared.analytics.spending import period_window
from cookbooks._shared.db import connect_readonly

_SUB_DEV_TOL_DEFAULT = 0.05
_OUTLIER_Z_DEFAULT = 2.0


@dataclass(frozen=True)
class AnomalyFinding:
    kind: Literal["subscription_drift", "merchant_outlier"]
    # subscription_drift fields
    subscription_id: str = ""
    transaction_id: str = ""
    expected: Decimal = Decimal("0")
    actual: Decimal = Decimal("0")
    delta_pct: float = 0.0
    # merchant_outlier fields
    merchant_id: str = ""
    period: str = ""
    this_month: Decimal = Decimal("0")
    monthly_mean: Decimal = Decimal("0")
    z_score: float = 0.0


def _resolve_tolerance(arg: float | None) -> float:
    if arg is not None:
        return arg
    raw = os.environ.get("PFH_SUB_DEV_TOL", "").strip()
    if not raw:
        return _SUB_DEV_TOL_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _SUB_DEV_TOL_DEFAULT


def _resolve_z(arg: float | None) -> float:
    if arg is not None:
        return arg
    raw = os.environ.get("PFH_OUTLIER_Z", "").strip()
    if not raw:
        return _OUTLIER_Z_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _OUTLIER_Z_DEFAULT


def detect_subscription_drift(
    period: str, tolerance: float | None = None,
) -> list[AnomalyFinding]:
    """Per-subscription transactions whose amount drifts beyond tolerance."""
    start, end = period_window(period)
    tol = _resolve_tolerance(tolerance)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            """
            SELECT t.id,
                   t.pattern_id,
                   CAST(p.expected_amount AS VARCHAR),
                   CAST(t.amount AS VARCHAR)
              FROM transactions t
              JOIN patterns p ON p.id = t.pattern_id
             WHERE t.date BETWEEN ? AND ?
            """,
            [start, end],
        ).fetchall()
    finally:
        conn.close()
    findings: list[AnomalyFinding] = []
    for tx_id, sub_id, expected_s, actual_s in rows:
        expected = Decimal(expected_s)
        actual = abs(Decimal(actual_s))
        if expected == 0:
            continue
        delta = float(abs(actual - expected) / expected)
        if delta > tol:
            findings.append(AnomalyFinding(
                kind="subscription_drift",
                subscription_id=sub_id,
                transaction_id=tx_id,
                expected=expected,
                actual=actual,
                delta_pct=delta,
            ))
    return findings


def detect_merchant_outliers(
    period: str,
    lookback_months: int = 6,
    z_threshold: float | None = None,
) -> list[AnomalyFinding]:
    """Merchants whose monthly spend in `period` is z>threshold from trailing mean."""
    z_thr = _resolve_z(z_threshold)
    start, end = period_window(period)
    # SQL: per-merchant per-month total spend across the window of (lookback + this month)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            """
            SELECT merchant_id,
                   strftime(date, '%Y-%m') AS ym,
                   CAST(SUM(ABS(CAST(amount AS DECIMAL(18,2)))) AS VARCHAR)
              FROM transactions
             WHERE merchant_id IS NOT NULL
               AND date < ?
             GROUP BY merchant_id, strftime(date, '%Y-%m')
            """,
            [end],
        ).fetchall()
        this_month_rows = conn.execute(
            """
            SELECT merchant_id,
                   CAST(SUM(ABS(CAST(amount AS DECIMAL(18,2)))) AS VARCHAR)
              FROM transactions
             WHERE merchant_id IS NOT NULL
               AND date BETWEEN ? AND ?
             GROUP BY merchant_id
            """,
            [start, end],
        ).fetchall()
    finally:
        conn.close()

    target_ym = period.replace("_", "-")
    history: dict[str, list[float]] = {}
    for mid, ym, total in rows:
        if ym == target_ym:
            continue  # exclude this month from history
        history.setdefault(mid, []).append(float(total))

    this_month_totals = {mid: Decimal(t) for mid, t in this_month_rows}

    findings: list[AnomalyFinding] = []
    for mid, this_total in this_month_totals.items():
        hist = history.get(mid, [])[-lookback_months:]
        if len(hist) < 2:
            continue  # need at least 2 prior months for stdev
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / len(hist)
        stdev = math.sqrt(var)
        if stdev == 0:
            # All prior months identical: infinite z if any deviation, finite otherwise
            if float(this_total) == mean:
                continue
            z = float("inf")
        else:
            z = (float(this_total) - mean) / stdev
        if abs(z) >= z_thr:
            findings.append(AnomalyFinding(
                kind="merchant_outlier",
                merchant_id=mid,
                period=period,
                this_month=this_total,
                monthly_mean=Decimal(str(round(mean, 2))),
                z_score=z if math.isfinite(z) else 999.0,
            ))
    return findings
