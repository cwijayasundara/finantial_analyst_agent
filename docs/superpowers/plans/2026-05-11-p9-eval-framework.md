# P9: Eval Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the spec's §10 + P6 gap — every cookbook (ingester, analyst, advisor, QA, forecast) ships a regression suite that fails loudly when LLM swaps, prompt edits, or refactors break a previously-good output. Without this, every future change is one prompt edit away from silently degrading the dashboard.

**Architecture:** A YAML-driven evaluator collected by pytest. Each cookbook gets an `evals/<suite>.yaml` whose rows describe a fixture, the cookbook trigger, and one or more **assertions** over the resulting state (deterministic checks) plus optional **LLM-as-judge** grading for memo prose / recommendation rationale. No new runtime — evals are pytest tests that share fixtures with the existing test suite. Judge runs on `ollama:qwen3:14b` (spec §10), with **graceful skip** when the model isn't pulled, so CI without GPUs still passes the deterministic layer.

**Privacy:** Eval inputs are synthetic ledgers (the same `tmp_workspace` fixtures we already use). No real PDFs, no PII. LLM-judge invocations stay local (Ollama loopback). Same `PFH_ALLOW_REMOTE_LLM` gate as everything else.

**Non-goals:** Full LangSmith / DSPy integration. Statistical significance testing. A web UI for browsing eval runs (CI markdown summary is enough).

---

## File Structure

```
eval/
├── __init__.py
├── runner.py                       # YAML loader + pytest-collected runner
├── matchers.py                     # deterministic assertion matchers
├── judge.py                        # LLM-as-judge wrapper (qwen3:14b, skippable)
├── report.py                       # JSON + Markdown summary writer
└── README.md

cookbooks/statement_ingester/evals/
└── ingestion_smoke.yaml            # 8 cases: synthetic PDF → ledger row checks

cookbooks/monthly_analyst/evals/
└── memo_quality.yaml               # 6 cases: ledger fixture → memo assertions + judge

cookbooks/advisor/evals/
└── recommendation_kinds.yaml       # 8 cases: variance/forecast/goal → expected kinds + citations

cookbooks/_shared/qa_evals/
└── nl_questions.yaml               # 12 cases: NL → expected SQL/Cypher shape + cited values

tests/eval/
├── test_eval_runner.py             # unit: YAML parsing, matcher dispatch, judge mock
├── test_ingestion_evals.py         # collects ingester suite
├── test_analyst_evals.py
├── test_advisor_evals.py
└── test_qa_evals.py
```

Eval YAML schema (single source of truth):

```yaml
suite: memo_quality
cookbook: monthly_analyst
description: Asserts memo sections render under representative ledgers.

cases:
  - id: groceries_overshoot_april_2025
    description: Spending well over target should produce an "over" badge + cite the budget
    fixture: april_2025_overshoot     # name of a pytest fixture builder
    trigger:
      period: 2025_04
    assertions:
      - kind: section_present
        section: "Budget Variance"
      - kind: contains_substring
        path: draft_body
        text: "⚠ [[budget_groceries_2025_04]]"
      - kind: citation_count_gte
        n: 3
      - kind: numeric_field
        path: state.budget_variance[0].pct
        op: gt
        value: 0.10
    judge:                # optional, only runs when qwen3:14b available
      rubric: ./judges/memo_clarity.md
      pass_threshold: 0.7
```

Matcher kinds (Task 2 list): `section_present`, `contains_substring`, `regex_match`, `citation_count_gte`, `numeric_field`, `field_equals`, `list_length`, `cypher_returns_row`, `sql_returns_row`. Each is a tiny pure function over the result dict.

---

## Task 1: Skeleton + YAML schema validation (`eval/runner.py`, `eval/matchers.py`)

- [ ] `EvalSuite` Pydantic model: `suite`, `cookbook`, `description`, `cases: list[EvalCase]`.
- [ ] `EvalCase`: `id`, `description`, `fixture`, `trigger`, `assertions`, `judge?`.
- [ ] `Assertion` is a tagged union (`kind` discriminator) — `model_validator` rejects unknown kinds at load time.
- [ ] `load_suite(path: Path) -> EvalSuite` with friendly error messages on the offending line.
- [ ] One pytest test that loads every suite under `cookbooks/**/evals/*.yaml` and asserts it parses cleanly — catches typos before they hit a CI run.

Tests: `test_eval_runner.py` — parses a happy-path YAML, rejects an unknown matcher kind, rejects a missing fixture name.

## Task 2: Deterministic matchers (`eval/matchers.py`)

- [ ] Implement each matcher as `def match_<kind>(result: dict, **kwargs) -> MatchOutcome`. `MatchOutcome` is `{passed: bool, detail: str}`.
- [ ] `section_present(result, section)` — splits `result["draft_body"]` on `## ` and checks the section header exists.
- [ ] `contains_substring(result, path, text)` — `dotted.path.in.result` lookup, then `text in value`.
- [ ] `numeric_field(result, path, op, value)` — supports `eq`, `lt`, `gt`, `gte`, `lte`, `approx` (within 1%).
- [ ] `citation_count_gte(result, n)` — counts `draft_citations`.
- [ ] `cypher_returns_row(result, query, expected_first_row)` — runs cypher against the seeded fixture's kuzu DB.
- [ ] `sql_returns_row(result, sql, expected)` — same against DuckDB.

Tests: unit tests for each matcher in isolation against synthetic dicts.

## Task 3: Cookbook trigger adapter

The runner needs a uniform way to invoke each cookbook from a YAML row.

- [ ] `eval/adapters/__init__.py` — registry mapping `cookbook` → callable `(fixture_name, trigger_dict) -> result_dict`.
- [ ] `adapters/monthly_analyst.py` — invokes the analyst graph end-to-end on the named fixture, returns the final state.
- [ ] `adapters/advisor.py` — invokes the advisor on a memo + variance + forecast snapshot; returns final state with the recommendations list.
- [ ] `adapters/statement_ingester.py` — calls the ingester CLI's `run` against a synthetic PDF path stored under `tests/fixtures/ingester/*.pdf`.
- [ ] `adapters/qa.py` — calls `/api/qa/ask-sync` against the seeded TestClient.

Each adapter is ≤ 40 lines; they're glue, not logic.

Tests: each adapter has one round-trip test using an existing fixture.

## Task 4: Fixture catalogue (`tests/eval/fixtures/`)

The evals reference fixtures by **name** — those names map to `pytest fixture` builders so the existing fixture toolkit is reused (no parallel infra).

- [ ] `april_2025_overshoot`: budget £100, actual £180 on groceries — for analyst variance evals.
- [ ] `goals_on_track_2025_06`: a savings goal £8000 by 2025-09 with £500 inflows.
- [ ] `forecast_uptrend_groceries`: 12 months of rising grocery spend → expects Holt method.
- [ ] `forecast_seasonal_two_years`: 24 months with December spike → expects seasonal_naive.
- [ ] `advisor_forecast_overshoot`: history projects >10% over budget → expects `forecast_overshoot` rec.
- [ ] `qa_groceries_history`: 6 months of categorised txns for "how much at Tesco in Feb 2025?"

Each fixture is a thin builder over the existing `tmp_workspace` + `connect_readwrite` helpers — no real PDFs.

## Task 5: LLM-as-judge (`eval/judge.py`)

- [ ] `Judge(model="ollama:qwen3:14b")` — wraps `cookbooks._shared.llm.build_chat_model` but pinned to a separate judge model (not the model under test).
- [ ] Probe-once-per-session: if the judge model is not pulled in Ollama, log a single info line and **skip every judge invocation** (don't fail). This keeps CI fast on machines without `qwen3:14b`.
- [ ] Rubric prompt:
  ```
  You are grading a financial memo. Given the rubric and the memo, output JSON:
  {"score": 0..1, "justifications": [str]}.
  Pass threshold: 0.7. Be strict about citation discipline and numeric accuracy.
  ```
- [ ] Returns `JudgeOutcome { score, passed, justifications, skipped }`.

Tests: monkeypatch the model client; assert pass/fail thresholds + skip behaviour.

## Task 6: Pytest collectors (`tests/eval/test_*_evals.py`)

- [ ] `pytest_generate_tests` reads `cookbooks/<name>/evals/*.yaml`, expands each row into a parametrised test case named `<suite>::<case_id>`.
- [ ] Each parametrised test:
  1. Resolves the named fixture via `request.getfixturevalue(case.fixture)`
  2. Runs the cookbook adapter
  3. Runs every assertion; collects failures
  4. If `judge` present and not skipped, runs the judge and includes its outcome
  5. Asserts at least the deterministic assertions pass; judge failure is a warning unless the suite sets `judge_required: true`
- [ ] Pytest IDs are stable and human-readable so CI logs show what regressed.

Tests: golden run — `pytest tests/eval -q` passes on the seeded suites.

## Task 7: Seed the 4 cookbook suites

- [ ] `cookbooks/statement_ingester/evals/ingestion_smoke.yaml` (8 cases) — fixture is a synthetic two-page PDF the ingester already handles in `tests/statement_ingester/test_graph_e2e.py`. Checks: row count, sha256-skip-on-second-run, categorisation pass count, completeness warnings under threshold.
- [ ] `cookbooks/monthly_analyst/evals/memo_quality.yaml` (6 cases) — variance, forecast, goal progress, anomaly section, net-worth section, citation discipline.
- [ ] `cookbooks/advisor/evals/recommendation_kinds.yaml` (8 cases) — budget_overspend, forecast_overshoot, subscription_drift, goal_behind, debt_avalanche, debt_snowball, savings_underutilised, recommendation_ranking by expected savings.
- [ ] `cookbooks/_shared/qa_evals/nl_questions.yaml` (12 cases) — NL → tool-call sequence + expected citation values. These are the **regression seeds** that catch QA quality drift across LLM swaps.

## Task 8: Reporter (`eval/report.py`)

- [ ] `EvalReport { suite, cases: [{id, passed, assertions, judge}], summary }`.
- [ ] JSON writer (`eval/out/report.json`).
- [ ] Markdown writer (`eval/out/report.md`) — table per suite, pass/fail counters, judge skip notes.
- [ ] Hook the writer into a pytest session-finish callback so a full `pytest tests/eval` run drops a fresh report.

Tests: golden snapshot of the markdown for a tiny suite.

## Task 9: CI gate

- [ ] `pyproject.toml`: add `eval` pytest marker.
- [ ] CI script section (or a `make eval` target): `pytest -m eval --strict-markers` — must pass before any phase tag.
- [ ] Update `README.md` Phase-9 row + add a "Regression evals" section with `pytest tests/eval` recipe.

## Task 10: Acceptance + tag p9-evals

- [ ] All deterministic assertions across the 4 suites pass (judge can be skipped).
- [ ] `pytest tests/ --ignore=tests/statement_ingester/test_real_backfill.py` still 100% green.
- [ ] `eval/out/report.md` exists and shows each cookbook's pass rate.
- [ ] Tag `p9-evals` at the merge commit; push tag.

---

## Order of operations

Tasks 1 → 2 → 3 → 4 unblock everything. Task 5 (judge) is **optional** for an initial green; suites can rely on deterministic matchers first and gain judge sections later. Suite seeding (Task 7) can run in parallel across the four cookbooks once Tasks 1–4 are done.

## Risks

- **`qwen3:14b` not pulled on CI** — addressed by the skip-when-absent design in Task 5.
- **Eval drift** — when we extend a memo template, deterministic substrings break. Mitigation: keep substrings short and structural ("⚠ [[budget_"), not prose snippets.
- **Test runtime** — 34 new tests; each cookbook fixture is < 1s. Total budget < 30s. If it grows past 60s, gate the judge layer behind `-m judge`.
