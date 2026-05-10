# Monthly Analyst Cookbook

Generates a monthly memo per (year, month) from the P1 ledger + graph.
Pure synthesis — no new sources are ingested here.

## Pipeline

```
load_period → compute_rollups → detect_anomalies → draft_memo → lint_memo
                                                              ↘ (errors) ↗
                                                                publish → report
```

Every node is pure-Python sync. The single optional LLM call is in
`draft_memo` when `PFH_MEMO_MODE=llm` (default `template`, deterministic).

## Quickstart

```bash
# Single period
python -m cookbooks.monthly_analyst analyse 2025-04

# Range (inclusive). Skips periods whose memo file already exists.
python -m cookbooks.monthly_analyst backfill-memos 2025-01 2026-04

# Replay: reconstruct what was live when a Decision was written.
python -m cookbooks.monthly_analyst replay decision_upsert_merchant_ingester_20260510T085523098
```

## Outputs

| Path | Contents |
|---|---|
| `wiki/memos/memo_<yyyy_mm>.md` | One memo per period, with `[[wikilinks]]` to cited statements + merchants |
| `wiki/decisions/decision_publish_monthly_memo_*.md` | Auto-emitted per memo write — captures actor, fingerprints, affects link to the memo |
| `data/openai_audit.jsonl` | Only populated when `PFH_MEMO_MODE=llm` and `PFH_ALLOW_REMOTE_LLM=true` |

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `PFH_MEMO_MODE` | `template` | `llm` switches `draft_memo` to chat-model polish (preserves all numbers + wikilinks) |
| `PFH_MEMO_LINT_WARN_ONLY` | `false` | When `true`, completeness lint findings are non-fatal |
| `PFH_SUB_DEV_TOL` | `0.05` | Subscription drift tolerance (fraction of expected amount) |
| `PFH_OUTLIER_Z` | `2.0` | Merchant z-score threshold for outlier flagging |
| `PFH_ALLOW_REMOTE_LLM` | `false` | Only consulted when `PFH_MEMO_MODE=llm`; preserves the P1 privacy contract |

## What an analyst actually does

1. **Loads** every statement that overlaps the period (credit + savings accounts).
2. **Rollups** — `category_totals`, `merchant_totals(top_n=10)`, `account_balance_delta`. Pure DuckDB SQL, no LLM.
3. **Anomalies** — flags subscription drift (transaction amount vs `expected_amount` from `patterns` table) and merchant outliers (z-score vs trailing 6-month spend).
4. **Drafts** the memo using a deterministic template. Optional LLM polish.
5. **Lints** the body — every `£X.XX` and `X%` token must trace back to a cited rollup or finding. Default raises on drift; warn-only via env.
6. **Publishes** via the `publish_monthly_memo` action — writes the memo + emits a Decision wiki page.

## Skills

- [memo-rubric.md](skills/memo-rubric.md) — body shape and tone
- [anomaly-thresholds.md](skills/anomaly-thresholds.md) — when to flag, when to ignore
