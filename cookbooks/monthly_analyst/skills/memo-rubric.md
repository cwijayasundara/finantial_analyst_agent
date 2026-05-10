# Monthly Memo Rubric

A monthly memo is a 5–8 paragraph summary of a single period. Audience:
the user reading their own ledger six months from now, asking "what
happened in April?".

## Body shape (sections, in order)

1. **Summary** — one sentence per account. Total in/out, headline merchants.
2. **Top Categories** — bullet list, sorted descending by absolute spend.
3. **Top Merchants** — bullet list, top 10. Each as `[[merchant_<id>]]`.
4. **Anomalies** — every subscription drift + every merchant z-score outlier, one bullet each. Empty section is allowed.
5. **Account Net Flow** — per-account signed delta for the period.

## Hard requirements

- **No fabricated numbers.** Every `£X.XX` or `X%` in the body must trace
  back to a cited rollup or a finding. The `lint_memo` node enforces this
  by default (raises `MemoCompletenessError`).
- **Wikilinks for every entity.** Statements, merchants, subscriptions,
  accounts, categories — all linked via `[[<page_id>]]`. This is what
  Obsidian's graph view consumes.
- **Title-case month** in the header (`# Monthly Memo · April 2025`).
- **No PII.** Even though we're in template mode mostly, prefer category
  names over merchant names if the merchant looks personal (e.g. P2P
  transfers).

## Tone

Terse and factual. The memo is a record, not a story. Avoid adjectives
("unusually", "surprisingly") — let the anomaly findings speak.

## Length

200–600 words for a typical month. Empty months still get a memo with
"(no activity)" placeholders so the time series stays gap-free.

## What to skip

- Don't recompute totals; they're in `state["category_totals"]` etc.
- Don't make recommendations — that's the advisor's job (P3+).
- Don't classify a merchant — `categorise_node` already did that in P1.
