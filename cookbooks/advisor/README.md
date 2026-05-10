# Advisor Cookbook

Reads memos + budgets + decisions → drafts actionable Recommendations →
publishes via the action layer with full Decision provenance.

## Quickstart

```bash
# Run on one period
python -m cookbooks.advisor recommend 2025-04

# List concept reviews waiting for the user
python -m cookbooks.advisor review

# Mark a recommendation accepted
python -m cookbooks.advisor accept rec_2025_04_a3f1b2c8

# Or dismiss with a reason
python -m cookbooks.advisor dismiss rec_2025_04_a3f1b2c8 --reason "already cancelled"
```

## Pipeline

```
load_context → flag_uncertainties → draft_recommendations
            → lint_recommendations → publish_recommendations → report
```

## Outputs

| Path | What |
|---|---|
| `wiki/recommendations/rec_<period>_<hash>.md` | One per draft. Status `proposed → accepted | dismissed` |
| `wiki/annotations/concept_<id>_<hash>.md` | One per uncertainty queued for human review |
| `wiki/decisions/decision_publish_recommendation_*.md` | Auto-emitted by the action layer |
| `wiki/decisions/decision_flag_concept_review_*.md` | Auto-emitted by the action layer |

## Recommendation kinds

| Kind | Trigger |
|---|---|
| `subscription_cancel` | Subscription drift > 50% |
| `budget_adjust` | Over-budget by ≥ 20% |
| `anomaly_investigate` | Merchant z-score > 3 |
| `category_recategorise` | Generic canonical name (Other / Unknown / X) |

See [skills/recommendation-rubric.md](skills/recommendation-rubric.md).

## Privacy

Same contract as the rest of the system: ollama-default, opt-in OpenAI
via `PFH_ALLOW_REMOTE_LLM=true`, masker + audit log + post-mask guard
all apply.
