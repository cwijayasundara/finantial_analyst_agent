"""Goal attainment scoring.

Given an active Goal, computes how far the user is along the path:
where they would need to be at this point in time vs. where they
actually are. The advisor consumes the resulting `GoalProgress` to
emit `goal_off_track` recommendations when the user is meaningfully
behind plan.

Scope handlers (per `Goal.scope_type`):

- `savings_account`  — accumulation goal. `current_amount` is the
  net inflow into `scope_id` since `started_at` (sum of signed
  `account_balance_delta` per month). Target is £X by `target_date`.
- `debt_payoff`      — paydown goal. `current_amount` is the reduction
  in outstanding balance since `started_at`. Tasks 7+ extract APR /
  outstanding from credit statements; until then this returns 0.
- `category_underspend` — discipline goal: target_amount is the cap
  over the lifetime of the goal; current_amount is the cumulative
  spend in that category since started_at.
- `custom`           — read-only in v1; current_amount stays at 0.
"""
from __future__ import annotations

import os
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from cookbooks._shared.analytics.spending import period_window
from cookbooks._shared.db import connect_readonly

_TOL_DEFAULT = 0.05


@dataclass(frozen=True)
class GoalProgress:
    goal_id: str
    name: str
    scope_type: str
    scope_id: str
    target_amount: Decimal
    target_date: str
    started_at: str | None
    current_amount: Decimal
    pct_complete: float
    months_total: int
    months_elapsed: int
    monthly_required: Decimal
    on_track: bool
    status: Literal["on_track", "behind", "ahead", "achieved", "missed"]


def _resolve_tolerance() -> float:
    raw = os.environ.get("PFH_GOAL_TOLERANCE", "").strip()
    if not raw:
        return _TOL_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _TOL_DEFAULT


def _parse_date(s: object) -> date | None:
    """Accept a `date`, `datetime`, ISO string, or None."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        try:
            return datetime.fromisoformat(str(s)).date()
        except ValueError:
            return None


def _months_between(a: date, b: date) -> int:
    """Inclusive count of calendar months covered between a and b
    (e.g. 2025-01-01 → 2025-04-30 spans 4 months: Jan/Feb/Mar/Apr)."""
    if b < a:
        return 0
    return (b.year - a.year) * 12 + (b.month - a.month) + 1


def _period_end(period: str) -> date:
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, monthrange(year, month)[1])


def _current_amount_savings(scope_id: str, started_at: date, end: date) -> Decimal:
    """Sum signed amount changes for `scope_id` between started_at and end."""
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(CAST(amount AS DECIMAL(18,2))), 0) "
            "FROM transactions WHERE account_id=? AND date BETWEEN ? AND ?",
            [scope_id, started_at, end],
        ).fetchone()
    finally:
        conn.close()
    return Decimal(str(row[0])) if row else Decimal("0")


def _current_amount_underspend(scope_id: str, started_at: date, end: date) -> Decimal:
    """Cumulative spend in a category since started_at."""
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))), 0) "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE c.name=? AND t.date BETWEEN ? AND ?",
            [scope_id, started_at, end],
        ).fetchone()
    finally:
        conn.close()
    return Decimal(str(row[0])) if row else Decimal("0")


def goal_progress(goal_id: str, as_of_period: str) -> GoalProgress:
    """Compute attainment for one goal at the end of `as_of_period`."""
    period_window(as_of_period)  # validate format

    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT id,name,target_amount,target_date,scope_type,scope_id,"
            "status,started_at FROM goals WHERE id=?",
            [goal_id],
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise KeyError(f"goal {goal_id!r} not found")

    (gid, name, target_amount, target_date, scope_type, scope_id,
     stored_status, started_at_str) = row

    target_amount = Decimal(str(target_amount))
    target_date_d = _parse_date(target_date)
    started_at_d = _parse_date(started_at_str) or _period_end(as_of_period)
    end = _period_end(as_of_period)

    months_total = _months_between(started_at_d, target_date_d) if target_date_d else 1
    months_elapsed = min(_months_between(started_at_d, end), months_total)

    if scope_type == "savings_account":
        current = _current_amount_savings(scope_id, started_at_d, end)
        # Inflow goals: positive net delta is progress.
        if current < 0:
            current = Decimal("0")
    elif scope_type == "category_underspend":
        # Underspend goals: current = (target_amount - actual_spend), but we
        # report progress in the same direction as savings (more is better).
        spent = _current_amount_underspend(scope_id, started_at_d, end)
        current = max(target_amount - spent, Decimal("0"))
    elif scope_type == "debt_payoff":
        # P7 Task 7 wires this once parsers extract outstanding balance.
        # Until then progress is unknown but the goal is still tracked.
        current = Decimal("0")
    else:
        current = Decimal("0")

    pct = float(current / target_amount) if target_amount > 0 else 0.0
    expected_pct = (months_elapsed / months_total) if months_total > 0 else 1.0
    monthly_required = (
        (target_amount - current) / max(months_total - months_elapsed, 1)
        if months_total > months_elapsed else Decimal("0")
    )

    tol = _resolve_tolerance()
    if stored_status == "achieved":
        status: str = "achieved"
        on_track = True
    elif stored_status == "missed":
        status = "missed"
        on_track = False
    elif current >= target_amount:
        status = "achieved"
        on_track = True
    elif months_elapsed >= months_total:
        status = "missed"
        on_track = False
    elif pct >= expected_pct - tol and pct <= expected_pct + tol:
        status = "on_track"
        on_track = True
    elif pct > expected_pct + tol:
        status = "ahead"
        on_track = True
    else:
        status = "behind"
        on_track = False

    return GoalProgress(
        goal_id=gid, name=name, scope_type=scope_type, scope_id=scope_id,
        target_amount=target_amount, target_date=str(target_date),
        started_at=str(started_at_d) if started_at_d else None,
        current_amount=current.quantize(Decimal("0.01")),
        pct_complete=pct, months_total=months_total,
        months_elapsed=months_elapsed,
        monthly_required=monthly_required.quantize(Decimal("0.01")),
        on_track=on_track,
        status=status,  # type: ignore[arg-type]
    )


def all_active_goals_progress(as_of_period: str) -> list[GoalProgress]:
    """Compute progress for every goal whose status is 'active'."""
    conn = connect_readonly()
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM goals WHERE status='active'"
        ).fetchall()]
    finally:
        conn.close()
    return [goal_progress(gid, as_of_period) for gid in ids]
