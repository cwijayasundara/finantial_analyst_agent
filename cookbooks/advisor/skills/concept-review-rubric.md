# Concept Review Rubric

Use `flag_concept_review` when the system is **uncertain** but a write
would still be required to resolve. The page goes to
`wiki/annotations/concept_<id>_<hash>.md` with status `open`.

## What to flag

| Kind | Example |
|---|---|
| `generic_canonical` | Merchant canonical name is "Other", "Unknown", "Name", or "X" |
| `low_confidence_categorisation` | Categoriser returned confidence < 0.3 |
| `multi_brand_surface` | Surface form contains 4+ uppercase brand tokens |

## What NOT to flag

- Subscription drift — that's a Recommendation, not a review item.
- Anomaly outliers — same.
- Anything the user has already accepted or dismissed.

## Severity

| Severity | Meaning |
|---|---|
| `info` | Cosmetic; the system works correctly even if you ignore it |
| `warn` | Likely incorrect; analytics may be affected |
| `error` | Definitely incorrect; numbers in memos may be misleading |

The user closes the review by either editing the wiki page's `status` to
`closed` or running `python -m cookbooks.advisor review` and acting on
the suggestions interactively.
