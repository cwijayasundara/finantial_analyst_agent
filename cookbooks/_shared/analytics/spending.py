"""Period-scoped spending rollups over the DuckDB ledger.

Pure read-side analytics: every function takes a period (yyyy_mm) and
returns typed dataclass rows. No LLM, no writes — safe to call from any
context. Used by the monthly-analyst cookbook to produce memos.
"""
from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cookbooks._shared.db import connect_readonly

_PERIOD_RE = re.compile(r"^(\d{4})[_-](\d{2})$")


@dataclass(frozen=True)
class CategorySpend:
    category: str
    total: Decimal
    txn_count: int


@dataclass(frozen=True)
class MerchantSpend:
    merchant_id: str
    canonical_name: str
    total: Decimal
    txn_count: int


def period_window(period: str) -> tuple[date, date]:
    """Inclusive [first_day, last_day] for a yyyy_mm or yyyy-mm string."""
    m = _PERIOD_RE.match(period)
    if not m:
        raise ValueError(
            f"period {period!r} must be 'yyyy_mm' or 'yyyy-mm' (e.g. '2025_04')"
        )
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range in period {period!r}")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def category_totals(period: str) -> list[CategorySpend]:
    """SUM(abs(amount)) per category for the period, ordered desc."""
    start, end = period_window(period)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            """
            SELECT c.name,
                   CAST(SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))) AS VARCHAR),
                   COUNT(*)
              FROM transactions t
              JOIN categories c ON c.id = t.category_id
             WHERE t.date BETWEEN ? AND ?
             GROUP BY c.name
             ORDER BY SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))) DESC
            """,
            [start, end],
        ).fetchall()
    finally:
        conn.close()
    return [
        CategorySpend(category=r[0], total=Decimal(r[1]), txn_count=int(r[2]))
        for r in rows
    ]


def merchant_totals(period: str, top_n: int = 10) -> list[MerchantSpend]:
    """Top-N merchants by SUM(abs(amount)) for the period, desc."""
    start, end = period_window(period)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.canonical_name,
                   CAST(SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))) AS VARCHAR),
                   COUNT(*)
              FROM transactions t
              JOIN merchants m ON m.id = t.merchant_id
             WHERE t.date BETWEEN ? AND ?
             GROUP BY m.id, m.canonical_name
             ORDER BY SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))) DESC
             LIMIT ?
            """,
            [start, end, int(top_n)],
        ).fetchall()
    finally:
        conn.close()
    return [
        MerchantSpend(
            merchant_id=r[0], canonical_name=r[1],
            total=Decimal(r[2]), txn_count=int(r[3]),
        )
        for r in rows
    ]


def account_balance_delta(period: str) -> dict[str, Decimal]:
    """Net change in balance per account for the period (sum of signed amounts).

    Negative = net outflow, Positive = net inflow. Useful as a sanity
    check against the reported opening/closing balance on the source
    statement.
    """
    start, end = period_window(period)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            """
            SELECT account_id,
                   CAST(SUM(CAST(amount AS DECIMAL(18,2))) AS VARCHAR)
              FROM transactions
             WHERE date BETWEEN ? AND ?
             GROUP BY account_id
            """,
            [start, end],
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: Decimal(r[1]) for r in rows}
