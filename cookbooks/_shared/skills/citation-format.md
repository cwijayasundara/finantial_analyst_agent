# Citation format

Every numeric claim in your answer MUST carry a citation pointing
back to the source. Format:

  [stmt::<statement-id> row <N>]

For aggregates, cite the **transaction range**:

  [stmt::<statement-id> rows N1-N2]

For a wiki-derived statement, cite the page id:

  [wiki::memo_2026_04]

## Why

The critic sub-agent (postgres_total_reconcile) checks every cited
sum against direct Postgres aggregates. If a citation is missing,
unverifiable, or wrong, the critic rejects the answer.

## Examples

**Bad:**
> You spent £342.18 at Costco in March.

**Good:**
> You spent £342.18 at Costco in March across 7 visits
> [stmt::a1b2 rows 12-18].

**Aggregating across statements:**
> Total grocery spend in Q1 was £1,204.55 [stmt::a1b2 rows 12-18,
> stmt::c3d4 rows 5-23, stmt::e5f6 rows 8-21].
