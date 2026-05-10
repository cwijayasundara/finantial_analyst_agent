"""Debt + APR amortisation primitives.

Pure-math helpers. No DB, no LLM. The advisor's
`credit_payoff_accelerate` recommendation composes these to surface
specific £ / month payoff suggestions when a user is paying minimums
on a high-APR balance.

`outstanding` is positive £. `apr` is a fraction (0.199 = 19.9 %).
`monthly_payment` is positive £.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

_MAX_MONTHS = 600        # 50-year safety cap
_SENTINEL_INFINITE = -1  # payoff_horizon return when interest ≥ payment


@dataclass(frozen=True)
class AmortPeriod:
    month: int
    principal: Decimal
    interest: Decimal
    balance: Decimal


def amortisation(
    outstanding: float | Decimal,
    apr: float | Decimal,
    monthly_payment: float | Decimal,
) -> list[AmortPeriod]:
    """Return the per-month schedule until balance reaches zero.

    If the payment doesn't cover the monthly interest, returns an
    empty list (caller should check via `payoff_horizon`).
    """
    bal = Decimal(str(outstanding))
    rate = Decimal(str(apr)) / Decimal(12)
    pay = Decimal(str(monthly_payment))
    if bal <= 0 or pay <= 0:
        return []
    if bal * rate >= pay:
        return []

    out: list[AmortPeriod] = []
    month = 0
    while bal > Decimal("0.01") and month < _MAX_MONTHS:
        month += 1
        interest = (bal * rate).quantize(Decimal("0.01"))
        principal = pay - interest
        if principal > bal:
            principal = bal
        bal = (bal - principal).quantize(Decimal("0.01"))
        out.append(AmortPeriod(
            month=month, principal=principal,
            interest=interest, balance=bal,
        ))
    return out


def payoff_horizon(
    outstanding: float | Decimal,
    apr: float | Decimal,
    monthly_payment: float | Decimal,
) -> int:
    """Months to clear the balance. Returns -1 if payment ≤ monthly interest."""
    sched = amortisation(outstanding, apr, monthly_payment)
    if not sched:
        return _SENTINEL_INFINITE
    return sched[-1].month


def total_interest(
    outstanding: float | Decimal,
    apr: float | Decimal,
    monthly_payment: float | Decimal,
) -> Decimal:
    """Total £ interest paid over the full payoff. 0 if never paid off."""
    return sum(
        (p.interest for p in amortisation(outstanding, apr, monthly_payment)),
        Decimal("0"),
    )


def recommended_payment(
    outstanding: float | Decimal,
    apr: float | Decimal,
    target_months: int,
) -> Decimal:
    """Solve for the monthly payment that clears `outstanding` in
    `target_months`. Uses the standard annuity formula.

    P = L · r / (1 − (1+r)^−n)
    """
    if target_months <= 0:
        raise ValueError("target_months must be positive")
    L = Decimal(str(outstanding))
    r = Decimal(str(apr)) / Decimal(12)
    if r == 0:
        return (L / Decimal(target_months)).quantize(Decimal("0.01"))
    factor = Decimal(1) - (Decimal(1) + r) ** -target_months
    return (L * r / factor).quantize(Decimal("0.01"))


def is_infinite(horizon_months: int) -> bool:
    return horizon_months == _SENTINEL_INFINITE
