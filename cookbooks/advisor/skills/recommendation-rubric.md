# Recommendation Rubric

Recommendations are advisory, not financial advice. Avoid imperative
prose ("do X"); prefer "consider X" or "you may want to review X".

## When to recommend

| Kind | Trigger |
|---|---|
| `subscription_cancel` | Subscription drift > 50% from `expected_amount` |
| `budget_adjust` | Actual spend > target by ≥ 20% in one period |
| `anomaly_investigate` | Merchant outlier with z-score > 3 |
| `category_recategorise` | Merchant whose canonical name is generic (Other / Unknown / Name) |

## Hard rules

1. Every numeric token in the body must trace to a cited rollup, finding,
   or budget — `lint_recommendations_node` enforces this with the
   `MemoCompletenessError` raise.
2. Every recommendation cites at least one wiki page via `[[wikilink]]`
   in `citations`.
3. Confidence ∈ [0, 1]. Default to 0.7 for template-mode drafts.

## Tone

Tentative. Decisions are the user's; the advisor surfaces signal.
