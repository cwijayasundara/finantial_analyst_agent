"""Zero-dependency monthly forecasting primitives.

Three methods, auto-selected by available history length:

- `seasonal_naive`     — for ≥ 24 months of history; repeats the prior
  year's value at the same month-of-year. Cheap, surprisingly hard to
  beat for noisy retail spend with annual seasonality.
- `holt_smoothing`     — for ≥ 6 months. Holt's linear method tracks
  both level and trend; defaults α=0.5, β=0.2 (env-tunable via
  `PFH_FORECAST_ALPHA` / `PFH_FORECAST_BETA`).
- `linear_projection`  — for ≥ 2 months. Least-squares slope + intercept.
- (< 2 months) — returns the global mean repeated.

`forecast_category(name)` pulls the trailing window from the ledger and
auto-picks. Returns a `CategoryForecast` with history, forecast,
RMSE on in-sample fit, and the chosen method.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from cookbooks._shared.db import connect_readonly

_ALPHA_DEFAULT = 0.5
_BETA_DEFAULT = 0.2


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class CategoryForecast:
    category: str
    history_periods: list[str]              # ['2024_05', ..., '2025_04']
    history: list[Decimal]                  # one per history_period
    forecast_periods: list[str]             # ['2025_05', '2025_06', '2025_07']
    forecast: list[Decimal]
    method: Literal["seasonal_naive", "holt_smoothing", "linear_projection", "mean"]
    rmse: Decimal                           # in-sample fit error
    monthly_average: Decimal


def linear_projection(
    history: list[Decimal], horizon: int,
) -> tuple[list[Decimal], float]:
    """Least-squares slope + intercept; project `horizon` periods forward."""
    n = len(history)
    if n < 2:
        mean = float(history[0]) if history else 0.0
        return [Decimal(str(round(mean, 2)))] * horizon, 0.0
    xs = list(range(n))
    ys = [float(v) for v in history]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    intercept = my - slope * mx
    fit = [intercept + slope * x for x in xs]
    rmse = math.sqrt(sum((ys[i] - fit[i]) ** 2 for i in range(n)) / n)
    out = [
        Decimal(str(round(max(intercept + slope * (n + h), 0.0), 2)))
        for h in range(horizon)
    ]
    return out, rmse


def holt_smoothing(
    history: list[Decimal], horizon: int,
    alpha: float | None = None, beta: float | None = None,
) -> tuple[list[Decimal], float]:
    """Holt's linear method. Track level + trend recursively."""
    a = alpha if alpha is not None else _env_float("PFH_FORECAST_ALPHA", _ALPHA_DEFAULT)
    b = beta if beta is not None else _env_float("PFH_FORECAST_BETA", _BETA_DEFAULT)
    n = len(history)
    if n < 2:
        return linear_projection(history, horizon)
    ys = [float(v) for v in history]
    level = ys[0]
    trend = ys[1] - ys[0]
    fit: list[float] = [level + trend]
    for t in range(1, n):
        prev_level = level
        level = a * ys[t] + (1 - a) * (level + trend)
        trend = b * (level - prev_level) + (1 - b) * trend
        if t + 1 < n:
            fit.append(level + trend)
    in_sample = [(ys[i] - fit[i - 1]) ** 2 for i in range(1, n)]
    rmse = math.sqrt(sum(in_sample) / len(in_sample)) if in_sample else 0.0
    out = [
        Decimal(str(round(max(level + (h + 1) * trend, 0.0), 2)))
        for h in range(horizon)
    ]
    return out, rmse


def seasonal_naive(
    history: list[Decimal], horizon: int, period: int = 12,
) -> tuple[list[Decimal], float]:
    """Use the value from `period` months ago. Falls back to mean when
    history is shorter than 2 × period."""
    n = len(history)
    if n < 2 * period:
        mean = float(sum(history)) / n if n > 0 else 0.0
        return [Decimal(str(round(mean, 2)))] * horizon, 0.0
    ys = [float(v) for v in history]
    fit = [ys[i - period] for i in range(period, n)]
    actuals = ys[period:]
    rmse = math.sqrt(
        sum((actuals[i] - fit[i]) ** 2 for i in range(len(actuals)))
        / len(actuals)
    )
    out = [
        Decimal(str(round(ys[n - period + (h % period)], 2)))
        for h in range(horizon)
    ]
    return out, rmse


def _future_periods(last: str, horizon: int) -> list[str]:
    """yyyy_mm + N → ['yyyy_mm+1', 'yyyy_mm+2', ...]"""
    year, month = int(last[:4]), int(last[5:7])
    out: list[str] = []
    for _ in range(horizon):
        month += 1
        if month > 12:
            year += 1; month = 1
        out.append(f"{year:04d}_{month:02d}")
    return out


def _gather_history(
    category: str, last_period: str, lookback: int,
) -> tuple[list[str], list[Decimal]]:
    """Pull monthly absolute spend per period over the trailing window."""
    last_year, last_month = int(last_period[:4]), int(last_period[5:7])
    # Compute period_start (lookback months before last_period)
    total = last_month - lookback + 1
    sy, sm = last_year, total
    while sm <= 0:
        sy -= 1; sm += 12
    start = date(sy, sm, 1)
    end = date(last_year, last_month, 28).replace(day=28)
    # Use end-of-month for the last period
    from calendar import monthrange
    end = date(last_year, last_month, monthrange(last_year, last_month)[1])

    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT strftime(t.date, '%Y_%m') AS p, "
            "       CAST(SUM(ABS(CAST(t.amount AS DECIMAL(18,2)))) AS VARCHAR) "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE c.name = ? AND t.date BETWEEN ? AND ? "
            "GROUP BY 1 ORDER BY 1",
            [category, start, end],
        ).fetchall()
    finally:
        conn.close()
    by_period = {r[0]: Decimal(r[1]) for r in rows}

    # Fill missing months with 0 so the series is contiguous.
    periods: list[str] = []
    values: list[Decimal] = []
    cy, cm = sy, sm
    while (cy, cm) <= (last_year, last_month):
        p = f"{cy:04d}_{cm:02d}"
        periods.append(p)
        values.append(by_period.get(p, Decimal("0")))
        cm += 1
        if cm > 12:
            cy += 1; cm = 1
    return periods, values


def forecast_category(
    category: str,
    last_period: str,
    horizon: int = 3,
    lookback: int = 12,
) -> CategoryForecast:
    """Auto-pick the best method by available history length."""
    history_periods, history = _gather_history(category, last_period, lookback)

    if len(history) >= 24:
        forecast, rmse = seasonal_naive(history, horizon)
        method: str = "seasonal_naive"
    elif len(history) >= 6:
        forecast, rmse = holt_smoothing(history, horizon)
        method = "holt_smoothing"
    elif len(history) >= 2:
        forecast, rmse = linear_projection(history, horizon)
        method = "linear_projection"
    else:
        m = float(sum(history)) / len(history) if history else 0.0
        forecast = [Decimal(str(round(m, 2)))] * horizon
        rmse = 0.0
        method = "mean"

    avg = (sum(history) / len(history)) if history else Decimal("0")
    return CategoryForecast(
        category=category,
        history_periods=history_periods,
        history=history,
        forecast_periods=_future_periods(last_period, horizon),
        forecast=forecast,
        method=method,  # type: ignore[arg-type]
        rmse=Decimal(str(round(rmse, 2))),
        monthly_average=avg.quantize(Decimal("0.01")),
    )
