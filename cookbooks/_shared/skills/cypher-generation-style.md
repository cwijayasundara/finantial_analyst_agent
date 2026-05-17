# Cypher generation style

The full schema is in `_generated_schema.md` (auto-generated from the
ontology). Use it for label / relationship names and ID shapes.

## Style rules

1. **Always use parameters**: `MATCH (m:Merchant {id: $id}) ...` not
   `MATCH (m:Merchant {id: 'merchant::costco'})`. The `cypher_read_only`
   tool takes a `params` dict.
2. **Always cap your query**: end with `LIMIT N` where N is the smallest
   useful number. The tool appends an implicit LIMIT 1000 if you forget,
   but explicit beats implicit.
3. **Resolve merchant names before filtering**: call `merchant_resolve`
   first to get canonical IDs, then filter on `m.id`. Free-text matches
   on `canonical_name` are fragile.
4. **Prefer specific labels** (`MATCH (n:Merchant)`) over generic
   patterns (`MATCH (n)`). The former uses the constraint index; the
   latter scans.
5. **Project named fields**: `RETURN m.id, m.canonical_name` not
   `RETURN m`. Cheaper transfer; the agent doesn't need every property.
6. **Aggregate in Cypher, not in code**: `RETURN sum(t.amount)` not
   "pull all transactions and sum them in Python".

## Worked examples

**Q: What did I spend at Costco last month?**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $merchant_id})
WHERE t.date >= $start_date AND t.date < $end_date
RETURN sum(t.amount) AS total, count(t) AS n
LIMIT 1
```
Params: `{merchant_id: 'merchant::costco', start_date: '2026-04-01', end_date: '2026-05-01'}`.

**Q: Spending at Costco broken down by month for the last year.**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $merchant_id})
WHERE t.date >= $start_date
RETURN substring(t.date, 0, 7) AS month, sum(t.amount) AS total
ORDER BY month
LIMIT 24
```

**Q: Top 10 merchants by spend in 2025.**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant)
WHERE t.date >= '2025-01-01' AND t.date < '2026-01-01'
RETURN m.id AS merchant_id, m.canonical_name AS name,
       sum(t.amount) AS total, count(t) AS n
ORDER BY total DESC
LIMIT 10
```

**Q: Year-over-year for groceries.**

```cypher
MATCH (t:Transaction)-[:IN_CATEGORY]->(c:Category {id: $cat_id})
RETURN substring(t.date, 0, 4) AS year, sum(t.amount) AS total
ORDER BY year
LIMIT 10
```

**Q: How is Costco categorised?**

```cypher
MATCH (m:Merchant {id: $merchant_id})<-[:AT_MERCHANT]-(t:Transaction)
      -[:IN_CATEGORY]->(c:Category)
RETURN c.name AS category, count(t) AS n
ORDER BY n DESC
LIMIT 5
```
