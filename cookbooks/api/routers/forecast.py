"""Forecast endpoints (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.analytics.forecast import (
    CategoryForecast, forecast_category,
)
from cookbooks._shared.analytics.spending import category_totals

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


def _serialise(f: CategoryForecast) -> dict:
    return {
        "category": f.category,
        "history_periods": list(f.history_periods),
        "history": [str(v) for v in f.history],
        "forecast_periods": list(f.forecast_periods),
        "forecast": [str(v) for v in f.forecast],
        "method": f.method,
        "rmse": str(f.rmse),
        "monthly_average": str(f.monthly_average),
    }


@router.get("/categories")
def list_category_forecasts(
    period: str = Query(..., description="yyyy_mm — the as-of period"),
    horizon: int = Query(3, ge=1, le=12),
    lookback: int = Query(12, ge=2, le=60),
    top_n: int = Query(8, ge=1, le=50),
) -> list[dict]:
    """Top-N categories by current-month spend, each with a forecast."""
    try:
        cats = sorted(category_totals(period),
                      key=lambda c: c.total, reverse=True)[:top_n]
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return [
        _serialise(forecast_category(c.category, period,
                                     horizon=horizon, lookback=lookback))
        for c in cats
    ]


@router.get("/categories/{category}")
def get_category_forecast(
    category: str,
    period: str = Query(..., description="yyyy_mm — the as-of period"),
    horizon: int = Query(3, ge=1, le=12),
    lookback: int = Query(12, ge=2, le=60),
) -> dict:
    try:
        f = forecast_category(category, period,
                              horizon=horizon, lookback=lookback)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return _serialise(f)
