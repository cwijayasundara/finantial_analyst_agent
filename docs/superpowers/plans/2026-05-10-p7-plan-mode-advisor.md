# P7: Plan-Mode Advisor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the advisor from rear-view ("what happened last month") into navigation ("what should you do to hit your plan"). Three concept layers added on top of the P5 advisor, all sharing the same memo + recommendation + Decision pipeline:

1. **Goals** — aspirational targets with deadlines (e.g., "save £6,000 by 2026-04 for holiday")
2. **Net-worth tracking** — multi-account roll-up of cumulative position
3. **Debt awareness** — APR + outstanding balance + payoff projection for credit accounts

Together they let the advisor make plan-level recommendations instead of tactical ones: *"you're 2 weeks behind on the holiday goal; consider reducing dining by £80/mo"* rather than *"dining was up 8% last month."*

**Architecture:** No new cookbooks. Three new ObjectTypes, two new actions, three new analytics primitives, four new memo + recommendation triggers. The existing `monthly_analyst` and `advisor` pipelines compose them via additional nodes.

---

## File Structure

```
cookbooks/_shared/ontology/object_types.yaml        # add Goal, NetWorthSnapshot
cookbooks/_shared/ontology/link_types.yaml          # add `targets`, `affects_balance_of`
cookbooks/_shared/ontology/action_types.yaml        # add set_goal, snapshot_net_worth
cookbooks/_shared/ontology/functions/actions.py     # implement both
cookbooks/_shared/db.py                             # add goals + net_worth_snapshots tables; extend statements with outstanding_balance/apr/min_payment

cookbooks/_shared/analytics/goals.py                # attainment scoring
cookbooks/_shared/analytics/net_worth.py            # monthly snapshot + Δ
cookbooks/_shared/analytics/debt.py                 # amortisation + projected payoff

cookbooks/monthly_analyst/nodes/compute_net_worth.py # snapshot at period end
cookbooks/monthly_analyst/nodes/compute_goals.py     # attainment per goal
cookbooks/monthly_analyst/nodes/draft_memo.py        # extend template: Goals + Net Worth + Debt sections

cookbooks/advisor/nodes/draft_recommendations.py    # add 3 new triggers

cookbooks/statement_ingester/cli.py                 # add `goal` + `networth` subcommands

cookbooks/api/routers/goals.py                      # CRUD
cookbooks/api/routers/net_worth.py                  # read

web/app/goals/page.tsx
web/app/networth/page.tsx

tests/_shared/analytics/test_goals.py
tests/_shared/analytics/test_net_worth.py
tests/_shared/analytics/test_debt.py
tests/_shared/test_actions.py                       # extend
tests/api/test_goals_router.py
tests/api/test_net_worth_router.py
```

---

## Task 1: Ontology additions

- [ ] **`Goal`** ObjectType:
  ```yaml
  - id: Goal
    description: A target dollar amount to reach by a deadline.
    required_properties: [name, target_amount, target_date, scope_type, scope_id, status]
    optional_properties: [notes, started_at, completed_at]
    identity_property: id
  ```
  `scope_type` ∈ `{savings_account, debt_payoff, category_underspend, custom}`. `status` ∈ `{active, paused, achieved, missed}`.

- [ ] **`NetWorthSnapshot`** ObjectType:
  ```yaml
  - id: NetWorthSnapshot
    description: Multi-account total position at a specific period boundary.
    required_properties: [period, total_amount, by_account]
    optional_properties: [notes]
    identity_property: id
  ```

- [ ] Link types: `targets` (Goal → Account|Category), `evaluated_in` (Goal → Memo)
- [ ] Action types: `set_goal` (scopes: `[system, analyst, advisor, user]`), `snapshot_net_worth` (scopes: `[system, analyst]`)

## Task 2: DB schema

- [ ] `CREATE TABLE goals (id, name, target_amount, target_date, scope_type, scope_id, status, started_at, completed_at, notes)`
- [ ] `CREATE TABLE net_worth_snapshots (id, period, total_amount, by_account JSON, computed_at)`
- [ ] Extend `statements`: optional columns `outstanding_balance DECIMAL(12,2)`, `apr DECIMAL(5,4)`, `min_payment DECIMAL(12,2)`, `payment_due_date DATE`
- [ ] Migration: add columns IF NOT EXISTS so existing DBs migrate cleanly
- [ ] Tests: schema migration is idempotent

## Task 3: Actions

- [ ] `upsert_goal(*, actor, name, target_amount, target_date, scope_type, scope_id, status='active', notes='')` — wiki/goals/<slug>.md + DB row + Decision
- [ ] `snapshot_net_worth(*, actor, period, total_amount, by_account)` — wiki/networth/snap_<period>.md + DB row + Decision
- [ ] Both flow through `_audit` so Decision pages auto-fire
- [ ] Idempotent on `(name, target_date)` for goals; on `period` for snapshots
- [ ] Tests for each action

## Task 4: Manifest record-ingester dispatch

- [ ] Wire `Goal` and `NetWorthSnapshot` into `cookbooks/_shared/record_ingester.py:_DISPATCH`
- [ ] Manifest example for `goals.csv` checked into `tests/fixtures/`
- [ ] Tests: bulk import of 5 goals via CSV manifest

## Task 5: Goals analytics (`_shared/analytics/goals.py`)

- [ ] `goal_progress(goal_id, as_of_period: str)` returns `GoalProgress`:
  ```
  {goal_id, target_amount, target_date, current_amount,
   pct_complete, monthly_required, on_track: bool,
   months_remaining, status_summary}
  ```
- [ ] `current_amount` derivation per `scope_type`:
  - `savings_account` — sum of `account_balance_delta` since `started_at` for the account
  - `debt_payoff` — opening_balance − current outstanding_balance for the credit account
  - `category_underspend` — target_amount − cumulative spend over period range
  - `custom` — user-tracked via separate progress writes (out of scope for v1)
- [ ] `on_track` = `current_amount ≥ target_amount × (months_elapsed / total_months)` within tolerance (`PFH_GOAL_TOLERANCE`, default 0.05)
- [ ] Tests: 4 fixtures covering each scope type + behind / on-track / ahead boundary cases

## Task 6: Net-worth analytics (`_shared/analytics/net_worth.py`)

- [ ] `compute_snapshot(period)` — for each account, take the most-recent statement's `closing_balance` ≤ period_end; sum into `total_amount`; return `(total, by_account_dict)`
- [ ] Until parsers extract `closing_balance` (Task 7), use `account_balance_delta` cumulative-from-zero as a placeholder; document this in a docstring
- [ ] `month_over_month_delta(period)` — diff against previous period's snapshot
- [ ] Tests: 3-account fixture; M-over-M; missing prior period returns delta=None

## Task 7: Debt analytics (`_shared/analytics/debt.py`)

- [ ] `amortisation(outstanding, apr, monthly_payment)` → list of `(month, principal, interest, balance)` until balance reaches zero
- [ ] `payoff_horizon(outstanding, apr, monthly_payment)` → integer months
- [ ] `interest_cost_at(payment, outstanding, apr)` → total £ interest over the horizon
- [ ] `recommended_payment(outstanding, apr, target_months)` → monthly £ to pay it off in target_months
- [ ] Tests: standard amortisation cases + edge cases (payment ≤ monthly interest → infinite horizon, returns sentinel)

## Task 8: Statement parser extension for credit-card formats

- [ ] Extend `cookbooks/statement_ingester/nodes/parse.py` (or a new `format_parsers.py` helper) to extract from credit-card markdown:
  - Outstanding balance / "balance to pay"
  - APR / interest rate
  - Minimum payment
  - Payment due date
- [ ] Plumb into `cookbooks/statement_ingester/nodes/upsert.py`'s `upsert_statement` call
- [ ] Tests: 3 real-format-parser cases (Halifax, generic) — fixtures must use synthetic statement text

## Task 9: `compute_net_worth` analyst node

- [ ] New node placed between `compute_rollups` and `budget_variance`
- [ ] Calls `_shared/analytics/net_worth.py:compute_snapshot(period)`
- [ ] Calls `snapshot_net_worth` action so the snapshot is persisted
- [ ] Adds `state["net_worth_snapshot"]` for the memo template

## Task 10: `compute_goals` analyst node

- [ ] New node after `compute_net_worth`
- [ ] For every active `Goal`, calls `goal_progress(goal_id, period)`
- [ ] Adds `state["goal_progress"]: list[GoalProgress]`

## Task 11: Memo template extension

- [ ] `nodes/draft_memo.py`: three new sections (only render when data present):
  ```
  ## Net Worth
  - Total: £X (Δ £Y month-over-month)
  - By account: ...
  
  ## Goals progress
  - [[goal_holiday_2026]]: £4,200 / £6,000 (70%, on track)
  - [[goal_credit_payoff]]: £1,200 / £2,400 (50%, behind by ~£200)
  
  ## Debt status
  - [[acct_credit_1588]]: £2,400 outstanding · APR 19.9% · min £75/mo (~36-month payoff)
  ```
- [ ] All numeric tokens added to `state["draft_cited_values"]` so memo_lint passes
- [ ] Sections omitted entirely when no data — keeps existing memos lint-clean

## Task 12: Advisor recommendation kinds

In `cookbooks/advisor/nodes/draft_recommendations.py`, add:

- [ ] `goal_off_track` — for any `GoalProgress` where `on_track=False`. Body: "you're £X behind on [[goal]] (need £Y/mo, currently averaging £Z)". Confidence: 0.7.
- [ ] `credit_payoff_accelerate` — for any credit account where `apr > 10%` AND `payoff_horizon(min_payment) > 24 months`. Body: "paying min £X = £Y interest over Z months; pay £A to clear in B months instead". Confidence: 0.6.
- [ ] `net_worth_decline` — when month-over-month Δ < 0 for two consecutive months. Body: "net worth fell £X across two months; review category-level changes". Confidence: 0.55.
- [ ] All cited values flow into `cited_values` so lint passes

## Task 13: CLI surface

- [ ] `cookbooks/statement_ingester/cli.py`:
  - `goal set <name> <target> <date> --scope-type <t> --scope-id <s>`
  - `goal list [--status active]`
  - `goal progress <goal_id> [--period yyyy_mm]`
  - `networth snapshot <period>` (re-runs `compute_snapshot` and persists)
  - `networth list`

## Task 14: API endpoints

- [ ] `cookbooks/api/routers/goals.py`: GET list, GET id, POST create, POST {id}/update-status
- [ ] `cookbooks/api/routers/net_worth.py`: GET list, GET {period}, POST snapshot (system-only)
- [ ] Privacy assertion test still passes
- [ ] Wire both routers in `server.py`

## Task 15: Web UI

- [ ] `web/app/goals/page.tsx` — table with progress bars, add-goal dialog, click-through to goal detail (rendered Markdown + advisor recommendations citing the goal)
- [ ] `web/app/networth/page.tsx` — line chart of total + by-account stacked bars (use Recharts; install when needed)
- [ ] Dashboard `/` adds a "Goals on track / behind" KPI tile
- [ ] Memo detail page picks up new sections automatically (MarkdownView already renders them)

## Task 16: Acceptance + tag

- [ ] All P1-P6 tests still pass + ≥30 new P7 tests
- [ ] Real-data flow:
  1. Define a savings goal: `goal set "holiday-2026" 6000 2026-04 --scope-type savings_account --scope-id acct_savings_main`
  2. Run analyst on a recent period: `monthly_analyst analyse 2025_04`
  3. Memo includes Goals + Net Worth + Debt sections (per data presence)
  4. Run advisor: `advisor recommend 2025_04`
  5. If behind plan, `goal_off_track` recommendation is published
- [ ] Tag: `p7-plan-mode`

---

## Out of scope for v1

- **Investment / brokerage tracking** — would need a new ObjectType `Position` + price feeds; defer to P8 if needed
- **Tax-aware reporting** — UK ISA / pension caps + capital-gains tracking; defer
- **Automatic goal progress writes** — for the `custom` scope type, the user manually updates progress; no auto-detection
- **Forecasting beyond linear extrapolation** — ARIMA / Prophet would be a separate capability
- **Currency conversion / multi-currency goals** — single-currency assumption holds

## Risks

| Risk | Mitigation |
|---|---|
| Net-worth depends on `closing_balance` extraction; Task 8 may fail on real format variations | Net-worth analytics have a `balance_delta`-cumulative fallback so the feature works even without parser changes |
| APR not always present in scanned statements | Make APR optional; advisor only fires `credit_payoff_accelerate` when present |
| Goal scope_type=custom invites scope creep | v1 only auto-tracks `savings_account` / `debt_payoff` / `category_underspend`; `custom` is read-only |
| New memo sections inflate body length | Sections omit when data missing; lint catches fabricated numbers |
