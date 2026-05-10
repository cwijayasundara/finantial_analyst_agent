# P4: Budget Manager — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the user declare monthly budgets per category (or per merchant), and have every monthly memo automatically include a Variance section that shows actual vs target with a [[Budget]] wikilink. Adds a `Budget` ObjectType to the ontology and `set_budget` to the action layer.

**Architecture:** Declarative — budgets are CSV manifests (using the P2 record-path ingester) plus a thin `set_budget` action for ad-hoc updates. The analyst's `compute_rollups` node grows a `budget_variance` step. No new cookbook required; everything plugs into the existing `monthly_analyst` graph.

---

## File Structure

```
cookbooks/_shared/ontology/object_types.yaml        # add Budget
cookbooks/_shared/ontology/link_types.yaml          # add `budgeted_in` (Category → Budget) and `target_for` (Budget → Category|Merchant)
cookbooks/_shared/ontology/action_types.yaml        # add set_budget
cookbooks/_shared/ontology/functions/actions.py     # add upsert_budget
cookbooks/_shared/db.py                             # CREATE TABLE budgets (id, period, scope_type, scope_id, target_amount, source)
cookbooks/_shared/analytics/budgets.py              # variance + rollup helpers
cookbooks/monthly_analyst/nodes/budget_variance.py  # new node, runs after compute_rollups
cookbooks/monthly_analyst/nodes/draft_memo.py       # extend template to include "## Budget Variance"
cookbooks/monthly_analyst/state.py                  # add budget_variance: list[BudgetVariance]
cookbooks/monthly_analyst/schemas.py                # add Budget, BudgetVariance pydantic models

tests/_shared/test_db.py                            # extend: budgets table migration
tests/_shared/test_actions.py                       # extend: upsert_budget
tests/_shared/analytics/test_budgets.py
tests/monthly_analyst/test_budget_variance.py
tests/fixtures/synthetic_budgets.csv
tests/fixtures/synthetic_budgets.manifest.yaml      # for the record-path ingester path
```

---

## Task 1: Ontology + DB schema

- [ ] Add `Budget` ObjectType to `object_types.yaml` (description, required_properties: `period, scope_type, scope_id, target_amount`, optional: `notes`)
- [ ] Add link types:
  - `target_for` — Budget → [Category, Merchant]
  - `evaluated_in` — Budget → Memo (so each memo cites the budgets it scored against)
- [ ] Add `set_budget` to `action_types.yaml` with scopes `[system, analyst, advisor]`
- [ ] Add `CREATE TABLE budgets` migration in `db.py`:
  ```
  id VARCHAR PK, period VARCHAR (yyyy_mm or 'annual:yyyy'),
  scope_type VARCHAR ('category' | 'merchant'),
  scope_id VARCHAR (matches categories.name or merchants.id),
  target_amount DECIMAL(12,2), source VARCHAR ('manual' | 'manifest')
  ```
- [ ] Tests: schema migration is idempotent; ontology loader sees `Budget`

## Task 2: `upsert_budget` action

- [ ] `upsert_budget(*, actor, period, scope_type, scope_id, target_amount, notes='')` writes wiki + DB + Decision page
- [ ] Wiki: `wiki/budgets/budget_<period>_<scope_type>_<scope_id>.md` with frontmatter (`type: Budget`, `period`, `scope_type`, `scope_id`, `target_amount`, `notes`, `updated`)
- [ ] Body has [[wikilink]] to the Category or Merchant the budget targets
- [ ] Idempotent on `(period, scope_type, scope_id)`; updates target_amount in place
- [ ] Tests: wiki page renders correct wikilink; DB upsert hits existing row on second call

## Task 3: Manifest path for bulk budgets

- [ ] Wire `Budget` into `cookbooks/_shared/record_ingester.py:_DISPATCH`
- [ ] User drops `budgets.csv` + `budgets.manifest.yaml`:
  ```yaml
  target_type: Budget
  identity:
    column: id
  mapping:
    period: period
    scope_type: scope_type
    scope_id: scope_id
    target_amount: target_amount
    notes: notes
  validation:
    required: [period, scope_type, scope_id, target_amount]
    scope_type_in: [category, merchant]
  ```
- [ ] Tests: 5-row CSV ingests cleanly via `python -m cookbooks.statement_ingester` extended dispatch

## Task 4: Variance helpers (`_shared/analytics/budgets.py`)

- [ ] `budget_variance(period) -> list[BudgetVariance]` — for every budget whose period matches, compute `actual - target` and `pct_of_target`
- [ ] Handles annual budgets (`annual:2026`) by spreading target/12 across the period's month
- [ ] Returns Pydantic models with `over | under | on_track` flag (using `PFH_BUDGET_TOLERANCE`, default `0.05`)
- [ ] Tests: 3 budgets (one over, one under, one on track) → assert correct flags

## Task 5: `budget_variance` analyst node

- [ ] New node placed between `compute_rollups` and `detect_anomalies`
- [ ] Wraps Task 4; populates `state["budget_variance"]`
- [ ] Adds budget IDs to citations so the memo's lint pass accepts the target/actual values
- [ ] Tests: node integration over a fixture ledger

## Task 6: Memo template extension

- [ ] `nodes/draft_memo.py` — add a "## Budget Variance" section between Anomalies and Account Net Flow
- [ ] Each row: `- [[budget_<period>_<scope_type>_<scope_id>]]: actual £X.XX vs target £Y.YY (over by Z.Z%)`
- [ ] Rules: section omitted entirely when no budgets exist for the period (memos remain valid for early adoption)
- [ ] Lint test: every variance figure in the body must be in `state["draft_cited_values"]`

## Task 7: CLI surface

- [ ] Extend `cookbooks/statement_ingester/cli.py` with `budget` subcommand:
  - `budget set <period> <scope_type> <scope_id> <amount>` → calls `set_budget` action
  - `budget list [period]` → prints table from DB
  - `budget ingest <csv>` → calls `record_ingester.ingest_records`
- [ ] Tests: each subcommand against a clean fixture workspace

## Task 8: Acceptance + tag

- [ ] 258 + ≥25 new tests pass
- [ ] Author `tests/fixtures/synthetic_budgets.csv` with 8 categories × 3 months and ingest it
- [ ] Re-run analyst backfill 2025-01..2026-05 — every memo for periods covered by budgets now has a Budget Variance section, citing wikilinks
- [ ] Tag: `p4-budget-manager`

---

## Out of scope
- Goal-based savings targets (multi-year accumulation)
- Real-time alerting (push notifications when threshold breached mid-period)
- Predictive budgets (auto-suggested from history) — could land in P5 advisor

## Risks
- Spreading annual budgets monthly distorts seasonal spend (December > July) — `notes` field exists so the user can override per-period
- Adding `Budget` to record-ingester dispatch needs careful ordering: ingest budgets BEFORE memos in the same script run
