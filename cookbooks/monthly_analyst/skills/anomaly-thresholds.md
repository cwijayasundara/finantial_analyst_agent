# Anomaly Thresholds

Two finding kinds. Both are tunable via env vars; both default to values
calibrated against the P1 33-month corpus.

## `subscription_drift`

A transaction whose amount differs from its `Subscription.expected_amount`
by more than `PFH_SUB_DEV_TOL` (default `0.05` = 5%).

| Tolerance | Effect |
|---|---|
| `0.02` (2%) | Catches FX-rate fluctuation on USD subs (Spotify, Netflix). High noise. |
| `0.05` (default) | Standard. Catches genuine plan upgrades or trials lapsing. |
| `0.20` | Only flags large changes (>20% — e.g. provider swap, plan upgrade). |

The finding includes both the `expected` and `actual` amount + the
`delta_pct` so the memo writer can render the diff.

## `merchant_outlier`

A merchant's spend in the target month that's > `PFH_OUTLIER_Z` (default
`2.0`) standard deviations from its trailing-6-month mean.

| Threshold | Effect |
|---|---|
| `1.5` | Catches mild deviations. Reasonable for steady spenders. |
| `2.0` (default) | Standard 95th-percentile-ish in a Gaussian. |
| `3.0` | Only flags large step-changes (~99.7th percentile). |

Edge cases:

- **History too short** (< 2 prior months): merchant is silently skipped.
  We don't flag merchants we don't yet have a baseline for.
- **Zero stdev** (all prior months identical): if this month deviates at
  all, it's flagged with `z=999.0` (sentinel for "infinite").

## When to override

Set `PFH_SUB_DEV_TOL` or `PFH_OUTLIER_Z` at run-time:

```bash
PFH_OUTLIER_Z=3.0 python -m cookbooks.monthly_analyst analyse 2025-12
```

Memo lint catches every numeric token in the body, so even with
liberal thresholds the memo stays internally consistent. Drift between
config and what's actually flagged shows up in the body.
