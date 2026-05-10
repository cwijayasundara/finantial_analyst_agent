# P2: Analyst + Monthly Memo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the analyst layer on top of the P1 ledger + graph + decision corpus. Produce monthly memos that summarise spending, surface anomalies, and track recurring subscriptions — all writes go through governed actions, every output emits a `Decision` page (pattern carried forward from P1's `actions._audit`), and the existing privacy posture (local-first LLM, masker, denylist, audit log) is preserved.

**Architecture:** A second LangGraph `StateGraph` — `monthly_analyst` — runs over a (year, month) window. Reads from DuckDB ledger + `wiki/decisions/*.md` + Kuzu graph. Writes one `wiki/memos/<yyyy_mm>.md` per period via `publish_monthly_memo` action (currently `NotImplementedError` stub in P1). No new sources are ingested in P2 — this layer purely synthesises what P1 already captured.

**Tech Stack:** Same as P1 (Python 3.12, LangGraph, DuckDB, Kuzu, Pydantic, Typer + Rich, ollama-default LLM with opt-in OpenAI). Adds: `numpy>=2.0` for spending statistics, `python-dateutil>=2.9` for period iteration. No new external services.

**Borrowings from `context_graphs`** (concrete features lifted into P2):
1. **Completeness lint** (P1's `validate.py` is warn-only) — port the numeric-pattern verifier from `context_graphs/agents/lint_agent.py:126-134` into a hard-fail mode for the memo writer.
2. **Manifest-driven Record-path ingester** — adopt the `<file>.manifest.yaml` pattern (`context_graphs/agents/record_ingester.py`) so users can drop `subscriptions.csv` next to bank PDFs and get deterministic non-LLM ingestion.
3. **Decision replay** (`context_graphs/agents/decision_replay.py`) — given a memo, reconstruct the wiki + ontology fingerprints recorded at write-time so analytics are reproducible.

---

## File Structure

```
cookbooks/_shared/analytics/__init__.py             # new
cookbooks/_shared/analytics/spending.py             # period rollups, top-N merchants/categories
cookbooks/_shared/analytics/anomalies.py            # deviates_from + z-score outliers
cookbooks/_shared/record_ingester.py                # manifest-driven CSV ingest (no LLM)

cookbooks/monthly-analyst/__init__.py
cookbooks/monthly-analyst/__main__.py
cookbooks/monthly-analyst/README.md
cookbooks/monthly-analyst/steering-examples.json
cookbooks/monthly-analyst/schemas.py                # MemoDraft, AnomalyFinding, RecurringDelta
cookbooks/monthly-analyst/state.py                  # AnalystState TypedDict
cookbooks/monthly-analyst/graph.py                  # StateGraph wiring
cookbooks/monthly-analyst/cli.py                    # `analyse <yyyy-mm>`, `backfill-memos`
cookbooks/monthly-analyst/nodes/__init__.py
cookbooks/monthly-analyst/nodes/load_period.py      # pulls transactions + decisions for window
cookbooks/monthly-analyst/nodes/compute_rollups.py  # category/merchant totals via DuckDB
cookbooks/monthly-analyst/nodes/detect_anomalies.py # surfaces deviates_from + outliers
cookbooks/monthly-analyst/nodes/draft_memo.py       # LLM-or-template body writer
cookbooks/monthly-analyst/nodes/lint_memo.py        # completeness gate (port of context_graphs)
cookbooks/monthly-analyst/nodes/publish.py          # invoke publish_monthly_memo action
cookbooks/monthly-analyst/skills/memo-rubric.md
cookbooks/monthly-analyst/skills/anomaly-thresholds.md

cookbooks/_shared/ontology/functions/replay.py      # decision_replay (lifted from context_graphs)

tests/_shared/test_record_ingester.py
tests/_shared/test_replay.py
tests/_shared/analytics/test_spending.py
tests/_shared/analytics/test_anomalies.py
tests/monthly_analyst/test_load_period.py
tests/monthly_analyst/test_compute_rollups.py
tests/monthly_analyst/test_detect_anomalies.py
tests/monthly_analyst/test_draft_memo.py
tests/monthly_analyst/test_lint_memo.py
tests/monthly_analyst/test_publish.py
tests/monthly_analyst/test_graph_e2e.py
tests/monthly_analyst/test_cli.py
tests/fixtures/synthetic_subscriptions.csv
tests/fixtures/synthetic_subscriptions.manifest.yaml
```

**Boundaries.** `_shared/analytics/` is reusable: P3+ advisor will import the same spending/anomaly primitives. The `monthly-analyst` cookbook owns only its node wiring + memo body + lint discipline. No node touches DuckDB directly except via `_shared/db.py`'s connection helpers; all writes go through `actions.py`.

---

## Task 1: Implement `publish_monthly_memo` action

**Currently:** `cookbooks/_shared/ontology/functions/actions.py:298` raises `NotImplementedError("publish_monthly_memo lands in P3")` — the P1 plan slated this for P3 but P2 is the right home.

- [ ] Replace the stub with a full implementation that:
  - Writes `wiki/memos/<period>.md` (period = `yyyy_mm`) with YAML frontmatter (`id`, `type: Memo`, `period`, `cites: [<page_ids>]`, `confidence`, `updated`)
  - Body sections: Summary · Top Merchants · Top Categories · Anomalies · Recurring Patterns · Open Questions
  - Emits `[[<wiki_page_id>]]` wikilinks for every cited statement, merchant, category, subscription
  - Calls `_audit("publish_monthly_memo", actor, fm, page_id)` so the Decision page fires automatically
- [ ] Allowed actor scopes: `analyst` only (matches `action_types.yaml`)
- [ ] Idempotent: re-running for the same period overwrites the memo and emits a *new* Decision (timestamps differ, but `_decision_affects` should add `affects: [memos/<period>]`)

**Acceptance criteria.**
- `tests/_shared/test_actions.py::test_publish_monthly_memo_writes_memo_and_decision` passes
- A memo for `2025-04` cites at least the 4 statements (2 credit + 2 savings) covering that period
- Re-running yields exactly one memo file and ≥2 decision pages (one per call)

---

## Task 2: Spending analytics primitives (`_shared/analytics/spending.py`)

- [ ] `period_window(yyyy_mm: str) -> tuple[date, date]` — first and last day inclusive
- [ ] `category_totals(period) -> list[CategorySpend]` — SUM(abs(amount)) grouped by category, ordered desc
- [ ] `merchant_totals(period, top_n=10) -> list[MerchantSpend]` — same shape, per merchant
- [ ] `account_balance_delta(period) -> dict[account_id, Decimal]` — for sanity-check alongside reported balances
- [ ] All queries via DuckDB — no LLM
- [ ] Pure functions; no I/O beyond `connect_readonly()`

**Tests.** `tests/_shared/analytics/test_spending.py` seeds a small fixture ledger and asserts totals match hand-computed values.

---

## Task 3: Anomaly detection (`_shared/analytics/anomalies.py`)

Two classes of finding:

- [ ] **Subscription deviation** — for each `Subscription`, find transactions where `abs(amount - expected_amount) / expected_amount > tolerance` (default 5%). Emit `AnomalyFinding(kind="subscription_drift", subscription_id, transaction_id, expected, actual, delta_pct)`.
- [ ] **Merchant outlier** — per merchant, compute mean ± stdev across the trailing 6 months of monthly spend; flag any month outside ±2σ. Emit `AnomalyFinding(kind="merchant_outlier", merchant_id, period, z_score, monthly_mean, this_month)`.
- [ ] All thresholds configurable via env (`PFH_SUB_DEV_TOL`, `PFH_OUTLIER_Z`).
- [ ] `link_types.yaml` already has `deviates_from: Transaction → Subscription`; the subscription-drift finder should write that edge into the graph (via the existing `compile_graph` reflow).

**Tests.** Hand-rolled fixture with one subscription drift and one merchant outlier; assert each is returned exactly once.

---

## Task 4: Manifest-driven Record-path ingester (`_shared/record_ingester.py`)

Borrowed from `context_graphs/agents/record_ingester.py`. Goal: declarative non-LLM ingestion for tabular data the user already understands (subscriptions, manual annotations).

- [ ] Accept a directory containing `<name>.csv` + `<name>.manifest.yaml`
- [ ] Manifest schema:
  ```yaml
  source_class: Subscription   # ObjectType id from ontology
  identity:
    column: subscription_id
    slug: "sub_${subscription_id}"
  mapping:
    target_type: Subscription
    columns:
      merchant_id: merchant_id
      cadence: cadence
      expected_amount: expected_amount
      last_seen: last_seen
      confidence: confidence
  validation:
    required: [merchant_id, cadence, expected_amount]
    cadence_in: [monthly, quarterly, annual, weekly]
  ```
- [ ] On run: validate column types against ontology, then loop rows calling the appropriate `upsert_*` action — Decision pages fire automatically
- [ ] Refuses to ingest if any row fails validation; reports row-level errors to stderr

**Tests.** `tests/_shared/test_record_ingester.py` with a fixture CSV + manifest. Assert that each row produces a wiki page + a Decision page; a deliberately-malformed row aborts the run.

---

## Task 5: Completeness lint (`monthly-analyst/nodes/lint_memo.py`)

Port of `context_graphs/agents/lint_agent.py:126-134`'s numeric-pattern verifier.

- [ ] Scan the drafted memo body for monetary tokens (`£X.XX`, `$X,XXX.XX`, `X.X%`)
- [ ] For each token, verify it appears in at least one cited transaction / rollup / anomaly. If not → finding `"unsupported_number"` with line + token
- [ ] Hard-fail by default (raise `MemoCompletenessError`); allow override via `PFH_MEMO_LINT_WARN_ONLY=true` env var (mirrors P1's `completeness_warn_only` config flag)

**Tests.** Two fixture memos: one with all numbers cited (passes), one with a fabricated `£99.99` (raises).

---

## Task 6: Period loader node (`load_period.py`)

- [ ] Input: `AnalystState{"period": "2025-04"}`
- [ ] Pulls transactions, statements, merchants involved, subscriptions touching the window
- [ ] Pulls Decision pages whose `ts` falls in the window (for "what did we decide this month?")
- [ ] Writes everything into state for downstream nodes

---

## Task 7: Compute rollups node (`compute_rollups.py`)

- [ ] Calls `_shared/analytics/spending.py` primitives
- [ ] Adds `category_totals`, `merchant_totals`, `account_balance_delta` to state

---

## Task 8: Detect anomalies node (`detect_anomalies.py`)

- [ ] Calls `_shared/analytics/anomalies.py`
- [ ] Adds `findings: list[AnomalyFinding]` to state

---

## Task 9: Draft memo node (`draft_memo.py`)

Two modes, controlled by `PFH_MEMO_MODE`:

- [ ] **`template`** (default, deterministic) — render a Jinja-style Markdown template using state. No LLM call.
- [ ] **`llm`** — pass rollups + findings to `build_chat_model()` with the memo rubric (`skills/memo-rubric.md`). Local-first: ollama by default, opt-in remote via `PFH_ALLOW_REMOTE_LLM=true` (P1 contract preserved). Masker + audit log already kick in via `_AuditingChat`.
- [ ] Output: `state["draft_memo"]: str` (markdown body, no frontmatter yet)

---

## Task 10: Lint memo node (`lint_memo.py`)

- [ ] Wraps the lint primitive from Task 5
- [ ] On hard-fail mode + finding present: state["errors"] populated, downstream `publish` node short-circuits
- [ ] On warn-only: state["warnings"] populated, publish proceeds

---

## Task 11: Publish node (`publish.py`)

- [ ] Calls `invoke_action("publish_monthly_memo", actor="analyst", inputs={...})`
- [ ] On success: `state["memo_page_id"]`
- [ ] On `PermissionError` (wrong actor scope): re-raise — surfaces a config bug

---

## Task 12: Decision replay (`_shared/ontology/functions/replay.py`)

Lifted from `context_graphs/agents/decision_replay.py:110-209`. Scope reduced to PFH's needs:

- [ ] `replay(decision_id: str) -> ReplayState` — reconstructs which wiki pages + ontology version were live at `ts`
- [ ] Compares recorded `wiki_fingerprint` / `ontology_fingerprint` (already written in P1's Decision pages) against current; flags drift
- [ ] Returns counts (`live_pages_at_ts`, `prior_decisions_count`, `fingerprint_drift: bool`)
- [ ] CLI surface: `python -m cookbooks.monthly_analyst replay <decision_id>` prints the report

**Tests.** Synthetic decision corpus across 3 timestamps; replay at the middle ts must show exactly the pages that existed up to (but not after) that ts.

---

## Task 13: LangGraph wiring (`monthly-analyst/graph.py`)

- [ ] `build_analyst_graph()` factory wires the 7 nodes: `load_period → compute_rollups → detect_anomalies → draft_memo → lint_memo → publish → report`
- [ ] Conditional edge: `lint_memo` errors short-circuit to `report`
- [ ] Returns compiled StateGraph, mirroring `cookbooks/statement-ingester/graph.py`

---

## Task 14: CLI (`monthly-analyst/cli.py`)

Typer app with three subcommands:

- [ ] `analyse <yyyy-mm>` — one period, prints rollup table + memo path on success
- [ ] `backfill-memos <from> <to>` — iterate months in range; resumable (skip if memo file already exists for that period)
- [ ] `replay <decision_id>` — invokes the replay primitive

**CLI tests** mock `build_chat_model` and assert the right number of memo files are written across a 3-month range.

---

## Task 15: Docs + steering

- [ ] `cookbooks/monthly-analyst/README.md` — quickstart (`analyse 2025-04`, `backfill-memos 2025-01 2025-12`)
- [ ] `cookbooks/monthly-analyst/steering-examples.json` — three worked examples
- [ ] Skills: `memo-rubric.md`, `anomaly-thresholds.md`
- [ ] Top-level `README.md` — append the cookbook table row + privacy reaffirmation (no new egress)

---

## Task 16: Acceptance gate + tag

- [ ] All 195+ existing P1 tests still pass (no regressions)
- [ ] New P2 test count target: ≥40 unit + 3 e2e
- [ ] `python -m cookbooks.monthly_analyst backfill-memos 2025-01 2026-04` produces 16 memos under `wiki/memos/`, ≥1 anomaly per memo on average, no `MemoCompletenessError`
- [ ] Each memo has a corresponding Decision page under `wiki/decisions/`
- [ ] Tag the merge: `p2-analyst`

---

## Out of scope (defer to P3+)

- Advisor recommendations (`publish_recommendation`) — needs a different scope and rubric
- Concept-review queue (`flag_concept_review`) — relies on memo readers raising flags
- Web-path ingester (the third leg of the context_graphs ingestion taxonomy)
- Real-time alerts on subscription drift — P2 produces monthly memos; alerting is separate infra
- Cross-period trend analysis (YoY comparisons) — P3 advisor territory

---

## Risk register

| Risk | Mitigation |
|---|---|
| Memo LLM mode burns OpenAI tokens on every run | Default mode is `template` (deterministic); LLM mode is opt-in. Audit log captures every call. |
| Anomaly thresholds too aggressive → memo noise | Thresholds env-configurable; default tuned against current 33-month corpus during Task 16 sweep. |
| `publish_monthly_memo` action's actor=`analyst` collides with existing test fixtures expecting `ingester` | Add `analyst` to `tests/conftest.py` actor allowlist; verify scope rejection still fires for other actors. |
| Manifest schema diverges from ontology over time | The validator loads ontology at run-time; an ontology change that breaks a manifest produces a deterministic error pointing at the offending column. |
| Decision-page fingerprint computation slows under high write rate | P1 measured ~3.6ms per audit at ~1000 wiki pages; P2 won't materially change that count. Re-measure after Task 16. |
