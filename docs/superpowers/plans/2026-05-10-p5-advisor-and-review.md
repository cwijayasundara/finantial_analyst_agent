# P5: Advisor + Concept Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the loop. The advisor reads memos + budgets + decisions and proposes actionable recommendations (subscription cancellations, budget adjustments, anomaly investigations). Every recommendation is published as a Markdown wiki page with citations + a Decision row, and any uncertain categorisations get queued for human review via `flag_concept_review`.

**Architecture:** A new `advisor` cookbook with a single `recommend` LangGraph that runs over a (year, month) or (year) window. Reads the analyst's monthly memos + the budget variance, looks for actionable patterns, and emits one or more `Recommendation` wiki pages. Sister command `review` lists open `flag_concept_review` items the user needs to triage.

---

## File Structure

```
cookbooks/_shared/ontology/object_types.yaml        # add Recommendation, ConceptReview
cookbooks/_shared/ontology/link_types.yaml          # `recommends_for` (Recommendation→Memo|Budget|Merchant), `flagged_in` (ConceptReview→Memo)
cookbooks/_shared/ontology/action_types.yaml        # publish_recommendation already declared, just implement
cookbooks/_shared/ontology/functions/actions.py     # implement publish_recommendation + flag_concept_review

cookbooks/advisor/__init__.py
cookbooks/advisor/__main__.py
cookbooks/advisor/README.md
cookbooks/advisor/cli.py                            # `recommend <yyyy_mm>`, `review`, `accept <recommendation_id>`
cookbooks/advisor/schemas.py                        # Recommendation, ConceptReview, AdvisorState
cookbooks/advisor/state.py
cookbooks/advisor/graph.py
cookbooks/advisor/nodes/load_context.py             # pulls memo + variance + recent decisions
cookbooks/advisor/nodes/draft_recommendations.py    # template + LLM modes
cookbooks/advisor/nodes/lint_recommendations.py     # reuse memo_lint primitive
cookbooks/advisor/nodes/publish_recommendations.py  # invokes publish_recommendation per rec
cookbooks/advisor/nodes/flag_uncertainties.py       # scans context for low-confidence categorisations
cookbooks/advisor/skills/recommendation-rubric.md
cookbooks/advisor/skills/concept-review-rubric.md

tests/_shared/test_actions.py                       # extend: publish_recommendation, flag_concept_review
tests/advisor/test_load_context.py
tests/advisor/test_draft_recommendations.py
tests/advisor/test_lint_recommendations.py
tests/advisor/test_publish_recommendations.py
tests/advisor/test_flag_uncertainties.py
tests/advisor/test_graph_e2e.py
tests/advisor/test_cli.py
```

---

## Task 1: Ontology additions

- [ ] Add `Recommendation` ObjectType:
  ```yaml
  - id: Recommendation
    description: A specific actionable suggestion derived from a memo or budget variance.
    required_properties: [period, kind, body, confidence, status]
    optional_properties: [accepted_at, dismissed_at, accepted_by]
  ```
  `kind` enum: `subscription_cancel | budget_adjust | anomaly_investigate | category_recategorise`
  `status` enum: `proposed | accepted | dismissed | superseded`
- [ ] Add `ConceptReview` ObjectType:
  ```yaml
  - id: ConceptReview
    description: A queued question for the human user to resolve manually.
    required_properties: [concept_id, kind, reason, status]
  ```
- [ ] Link types: `recommends_for`, `cites` (already exists, reuse), `flagged_in`
- [ ] Tests: ontology loader picks up new types

## Task 2: `publish_recommendation` action

- [ ] Replace the `NotImplementedError` stub
- [ ] Signature: `publish_recommendation(*, actor, period, kind, body_md, citations, confidence, status='proposed')`
- [ ] Wiki: `wiki/recommendations/rec_<period>_<short_hash_of_body>.md`
  - Short hash makes the page id stable for the same recommendation; lets re-runs idempotently overwrite
- [ ] Frontmatter has `status` so user can hand-edit it to `accepted` later
- [ ] Calls `_audit("publish_recommendation", ...)` so Decision auto-fires
- [ ] Tests: idempotent on (period, body); status transitions surface in Decision page

## Task 3: `flag_concept_review` action

- [ ] Replace the `NotImplementedError` stub
- [ ] Signature: `flag_concept_review(*, actor, concept_id, kind, reason, severity='info')`
- [ ] Writes `wiki/annotations/concept_<concept_id>_<short_hash>.md` (uses existing annotations dir)
- [ ] Body has `[[<concept_id>]]` wikilink so Obsidian shows it as a back-link on the offending entity
- [ ] Tests: concept page created; Decision auto-fires

## Task 4: Advisor pipeline (cookbook)

LangGraph: `load_context → flag_uncertainties → draft_recommendations → lint_recommendations → publish_recommendations → report`

- [ ] **load_context** node: pulls the memo for the requested period (`wiki/memos/memo_<period>.md`), parses its frontmatter for `cites`, then loads each cited budget/anomaly/merchant page. Optionally pulls the *previous* memo for trend.
- [ ] **flag_uncertainties** node: looks for low-confidence categorisations (`merchants.confidence < 0.5`) referenced in the memo, calls `flag_concept_review` for each
- [ ] **draft_recommendations** node: template mode per kind:
  - subscription_cancel: emit one rec for any subscription drift > 50% (likely cancellable)
  - budget_adjust: emit one rec when actual > target by ≥ 20% for two consecutive periods
  - anomaly_investigate: one rec per merchant_outlier with z > 3
  - category_recategorise: one rec for any merchant whose latest categorisation has confidence < 0.3
- [ ] LLM mode (`PFH_ADVISOR_MODE=llm`): polish the template with the rubric. Local-default LLM, opt-in remote via existing flag.
- [ ] **lint_recommendations** node: reuse `cookbooks/_shared/analytics/memo_lint.py` — every monetary token in the body must trace to citations
- [ ] **publish_recommendations** node: one `invoke_action("publish_recommendation", ...)` per drafted rec
- [ ] Tests: each node + e2e

## Task 5: CLI

- [ ] `recommend <yyyy_mm>` — run advisor for one period; prints recs as a table
- [ ] `review` — list open `ConceptReview` items (where `status: open` in the wiki frontmatter)
- [ ] `accept <recommendation_id>` — flips status to `accepted` (writes Decision)
- [ ] `dismiss <recommendation_id> [--reason "..."]` — flips status to `dismissed`
- [ ] Tests for each subcommand

## Task 6: Skills + docs

- [ ] `skills/recommendation-rubric.md` — when to recommend, tone, "show your work"
- [ ] `skills/concept-review-rubric.md` — what counts as low-confidence, when to defer to human
- [ ] `README.md` quickstart: `recommend 2025_04`, `review`, `accept rec_2025_04_a3f1`
- [ ] `steering-examples.json`

## Task 7: Acceptance + tag

- [ ] All P1+P2+P3+P4 tests still pass; new P5 tests ≥ 30
- [ ] Run `python -m cookbooks.advisor recommend 2025_04` against the real ledger:
  - At least 1 Recommendation page is written
  - At least 1 ConceptReview is flagged (low-confidence categorisations exist in the corpus)
  - Decision pages emitted for every action
- [ ] User accepts one recommendation via CLI → wiki page status is `accepted`, Decision recorded
- [ ] Tag: `p5-advisor`

---

## Definition of Done for the whole personal-finance-helper

- [ ] P1 + P2 + P3 + P4 + P5 tags shipped
- [ ] One unified workflow: `python -m cookbooks.statement_ingester backfill sources/` then `python -m cookbooks.monthly_analyst backfill-memos 2025-01 2026-05` then `python -m cookbooks.advisor recommend 2025_04` produces a coherent monthly view
- [ ] Obsidian graph view shows: Account → Statement → Merchant → Category → Budget; Memo → Recommendation; Decision back-links visible on every entity touched
- [ ] Local-default privacy contract intact end-to-end; audit log captures every remote LLM call

---

## Out of scope (for v1)
- Goal-based long-term planning (retirement, mortgage payoff projections)
- Multi-currency portfolio handling
- Web UI / Obsidian plugin (Markdown + CLI is the surface for v1)
- Forecasting via time-series models — recommendations are based on observed patterns, not predictions

## Risks
- Recommendations are advisory, not financial advice. The README must say so. Recommendation page bodies should avoid imperative tone (`prefer "consider X" over "do X"`).
- LLM-mode advisor could hallucinate recommendations not backed by data — mitigated by `lint_recommendations` reuse of memo_lint
- Tone too cautious → useless; too bold → liability. Calibrate via the rubric over a few real-data runs before tagging
