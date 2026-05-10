"""Net-worth snapshot computation.

For a given `period` (yyyy_mm), compute each account's running
position from the start of the ledger up to (and including) the
period_end. Sum across accounts → `total_amount`. Persist via
`snapshot_net_worth` action so future runs can compute month-over-
month deltas.

Until P7 Task 8 lands (statement parser extracts `closing_balance`),
position is derived from the cumulative sum of signed `amount` values
in `transactions` per account. This is "net flow since records began"
rather than "current balance"; the docstring on `_account_position`
explains the caveat. The delta math is unaffected — only the
absolute level shifts by each account's true opening balance, which
the user can correct manually via the `--opening` flag on the CLI
once that lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cookbooks._shared.analytics.spending import period_window
from cookbooks._shared.db import connect_readonly


@dataclass(frozen=True)
class NetWorthMonthlyDelta:
    period: str
    prev_period: str | None
    total: Decimal
    prev_total: Decimal | None
    delta: Decimal | None
    pct_change: float | None


def _account_position(account_id: str, end) -> Decimal:
    """Cumulative net flow into `account_id` through `end` (inclusive).

    Caveat: this is the *change* since the ledger started, not the
    true current balance. P7 Task 8 will switch to extracted statement
    `closing_balance` when available, which gives absolute level.
    """
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(CAST(amount AS DECIMAL(18,2))), 0) "
            "FROM transactions WHERE account_id=? AND date <= ?",
            [account_id, end],
        ).fetchone()
    finally:
        conn.close()
    return Decimal(str(row[0])) if row else Decimal("0")


def compute_snapshot(period: str) -> tuple[Decimal, dict[str, Decimal]]:
    """Return `(total_amount, {account_id: position})` for the period."""
    _, end = period_window(period)
    conn = connect_readonly()
    try:
        accounts = [r[0] for r in conn.execute(
            "SELECT id FROM accounts ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()
    by_account: dict[str, Decimal] = {
        aid: _account_position(aid, end) for aid in accounts
    }
    total = sum(by_account.values(), Decimal("0"))
    return total, by_account


def _prev_period(period: str) -> str:
    """yyyy_mm -> previous yyyy_mm."""
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return f"{year - 1:04d}_12"
    return f"{year:04d}_{month - 1:02d}"


def month_over_month_delta(period: str) -> NetWorthMonthlyDelta:
    """Compare this period's total to the previous period's snapshot."""
    current_total, _ = compute_snapshot(period)
    prev = _prev_period(period)
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT CAST(total_amount AS VARCHAR) "
            "FROM net_worth_snapshots WHERE period=?",
            [prev],
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return NetWorthMonthlyDelta(
            period=period, prev_period=None, total=current_total,
            prev_total=None, delta=None, pct_change=None,
        )
    prev_total = Decimal(row[0])
    delta = current_total - prev_total
    pct = float(delta / prev_total) if prev_total != 0 else None
    return NetWorthMonthlyDelta(
        period=period, prev_period=prev, total=current_total,
        prev_total=prev_total, delta=delta, pct_change=pct,
    )
