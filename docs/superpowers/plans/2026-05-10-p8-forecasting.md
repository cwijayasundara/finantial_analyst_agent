# P8: Forecasting + Trend Awareness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the analyst + advisor from rear-view ("what happened last month") to navigation ("what's likely in the next 3–6 months"). The advisor gains the ability to say *"at this pace you'll overshoot groceries by £120 by June"* instead of *"groceries up 8% last month"*. Closes the loop on plan-mode advice from P7 by adding projection.

**Architecture:** No new cookbook. One new analytics module (`forecast.py`), one new analyst node (`forecast_node`), one new memo section, one new advisor recommendation kind. Implementations are **zero-dependency** — simple linear extrapolation, Holt's exponential smoothing, and a trivial 12-month seasonal pass. No statsmodels / prophet / sklearn (those would weigh more than the rest of the codebase combined).

**Privacy:** Identical to every other layer. Forecasting is a deterministic statistical operation; no LLM calls. The optional LLM-mode memo polish still applies if `PFH_MEMO_MODE=llm` is set.

---

## File Structure

```
cookbooks/_shared/analytics/forecast.py             # new — primitives
cookbooks/monthly_analyst/nodes/forecast.py         # new node
cookbooks/monthly_analyst/nodes/draft_memo.py       # extend with `## Forecast` section
cookbooks/monthly_analyst/state.py                  # add forecast: list[...]
cookbooks/monthly_analyst/graph.py                  # wire forecast_node

cookbooks/advisor/nodes/load_context.py             # pull forecast for the period
cookbooks/advisor/nodes/draft_recommendations.py    # forecast_overshoot kind
cookbooks/advisor/state.py                          # add forecast: list[...]

cookbooks/statement_ingester/cli.py                 # forecast <category> [--horizon N]

cookbooks/api/routers/forecast.py                   # GET /api/forecast/categories

web/app/forecast/page.tsx                           # client chart
web/components/ForecastChart.tsx                    # SVG sparkline w/ projected dashed line
web/lib/api.ts                                      # forecast namespace + types

tests/_shared/analytics/test_forecast.py
tests/monthly_analyst/test_forecast_node.py
tests/api/test_forecast_router.py
```

---

## Task 1: Forecasting primitives (`_shared/analytics/forecast.py`)

Three methods, each pure / deterministic / no external deps:

- [ ] `linear_projection(history: list[Decimal], horizon: int)` — least-squares slope + intercept over the trailing `lookback`; project `horizon` months forward; return `(forecast: list[Decimal], rmse: float)`.
- [ ] `holt_smoothing(history, horizon, alpha=0.5, beta=0.2)` — Holt's linear method (level + trend). Standard recursion:
  ```
  level_t = alpha * y_t + (1 - alpha) * (level_{t-1} + trend_{t-1})
  trend_t = beta  * (level_t - level_{t-1}) + (1 - beta) * trend_{t-1}
  forecast_{t+h} = level_t + h * trend_t
  ```
- [ ] `seasonal_naive(history, horizon, period=12)` — repeat-the-prior-year baseline. When `len(history) < 2*period`, falls back to the global mean.
- [ ] `forecast_category(category, horizon=3, lookback=12)` — wraps the above:
  1. Gather monthly absolute spend per period from `transactions JOIN categories` over the trailing `lookback` months
  2. Choose method:
     - `seasonal_naive` if we have ≥ 18 months
     - `holt_smoothing` if we have ≥ 6 months
     - `linear_projection` otherwise (fallback to mean if < 2 months)
  3. Return `CategoryForecast { category, history, forecast, method, rmse, monthly_average }`

Tests: 12 cases covering each method's mathematics + the auto-select logic + missing-data fallbacks.

## Task 2: Savings-rate / goal-trajectory primitive

- [ ] `goal_trajectory(goal_id, lookback=6, horizon=3)` — given a `savings_account` goal, fit linear projection on the trailing 6 months of net inflow into the scope account; project where the goal will be in `horizon` months given current pace. Returns `GoalTrajectory { goal_id, projected_amount_at_target, gap_at_target, will_hit_target: bool }`.

Tests: typical / accelerating / decelerating savers.

## Task 3: `forecast_node` analyst node

- [ ] Lives between `budget_variance` and `detect_anomalies`
- [ ] For each category present in the period, calls `forecast_category(...)`; results land in `state["forecasts"]`
- [ ] Caps to top-8 categories by current-month spend to keep the memo short

## Task 4: Memo template extension

- [ ] `nodes/draft_memo.py`: new section `## Forecast (next 3 months)` rendered when `state["forecasts"]` is non-empty. Per category:
  ```
  - groceries · £105 this month · projected £108 / £112 / £109 (Holt, RMSE £14)
  ```
- [ ] All cited numbers flow into `state["draft_cited_values"]` so `memo_lint` passes

## Task 5: Advisor `forecast_overshoot` kind

- [ ] For each `BudgetVariance` with a matching `CategoryForecast`, project whether the cumulative spend across the budget's remaining months will exceed the target
- [ ] Trigger when projected_cumulative > target * 1.10 (10% buffer)
- [ ] Body cites:
  - current pace per month
  - projected cumulative
  - £ overshoot at end of period
  - suggested monthly cap to stay on plan
- [ ] Confidence 0.6 (forecasts are uncertain)

## Task 6: CLI surface

- [ ] `python -m cookbooks.statement_ingester forecast <category> [--horizon N] [--lookback M]`
- [ ] Pretty Rich table: history + projected + method + RMSE

## Task 7: API endpoints

- [ ] `GET /api/forecast/categories` — all categories' next-3-month projection
- [ ] `GET /api/forecast/categories/{name}?horizon=&lookback=`
- [ ] `GET /api/forecast/goals/{goal_id}` — goal trajectory
- [ ] Tests: against the populated fixture ledger

## Task 8: Web UI

- [ ] `/forecast` page lists categories with sparklines (history dashed, projection solid)
- [ ] Components/ForecastChart.tsx — small inline SVG renderer (no chart lib). Trailing 6 months + projected 3 months
- [ ] Dashboard: add a "Categories trending up" KPI tile

## Task 9: Acceptance + tag

- [ ] All P1-P7 tests still pass + ≥25 new P8 tests
- [ ] Run advisor against the real ledger; verify `forecast_overshoot` fires for any category with realistic upward trend + budget set
- [ ] Tag: `p8-forecasting`

---

## Out of scope

- Prophet / statsmodels / sklearn — too heavy for the value
- Confidence intervals — RMSE-only; presenting prediction bands requires more math than a single-user app needs
- Daily / weekly forecasting — monthly granularity matches the rest of the system
- Forecast of multi-category goals — only single-account savings goals get trajectories in v1
- Forecasting income — only outflow categories; income is sparser + lumpy and harder to predict

## Risks

| Risk | Mitigation |
|---|---|
| Short history → wild forecasts | Auto-select method by data length; fall back to mean below 2 months |
| Holt parameters need tuning | Default alpha=0.5 / beta=0.2 — robust for noisy monthly retail data; env-tunable via `PFH_FORECAST_ALPHA` / `PFH_FORECAST_BETA` |
| Overshoot triggers too eagerly | 10% buffer + min-12-months-of-history gate before issuing the recommendation |
| Forecast section bloats memo body | Capped to top-8 categories by current spend |
