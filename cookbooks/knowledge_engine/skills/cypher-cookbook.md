# Cypher Cookbook

The compiled Kuzu graph stores **all node types under a single
`Entity` table**, with a string `type` property and a string `props`
column holding JSON for the rest. Edges are typed (one table per link
type — `categorised_as`, `at_merchant`, `from_account`, etc.).

## Boilerplate

```cypher
-- All merchants
MATCH (m:Entity) WHERE m.type = 'Merchant' RETURN m.id LIMIT 50

-- One specific merchant
MATCH (m:Entity {id: 'amazon'}) RETURN m.id, m.props
```

## Common queries

### Top merchants by transaction count

```cypher
MATCH (t:Entity)-[:at_merchant]->(m:Entity)
WHERE t.type = 'Transaction' AND m.type = 'Merchant'
RETURN m.id AS merchant, count(*) AS n
ORDER BY n DESC
LIMIT 10
```

### Merchants in a category

```cypher
MATCH (m:Entity)-[:categorised_as]->(c:Entity)
WHERE m.type = 'Merchant' AND c.type = 'Category' AND c.id = 'groceries'
RETURN m.id LIMIT 50
```

### Statements for a given account

```cypher
MATCH (t:Entity)-[:from_account]->(a:Entity)
WHERE a.id = 'acct_credit_1588'
WITH DISTINCT t
MATCH (t)-[:in_statement]->(s:Entity)
RETURN DISTINCT s.id LIMIT 100
```

### Subscriptions deviating in a period

```cypher
MATCH (t:Entity)-[:deviates_from]->(s:Entity)
WHERE t.type = 'Transaction' AND s.type = 'Subscription'
RETURN t.id AS txn, s.id AS sub
```

## Forbidden

`CREATE`, `MERGE`, `DELETE`, `SET`, `DROP`, `ALTER`, `REMOVE`, `DETACH`
are blocked at the executor. To mutate, use the action layer
(`upsert_merchant`, `merge_merchant_aliases`, etc.).
