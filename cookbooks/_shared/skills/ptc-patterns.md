# Programmatic Tool Calling patterns

DeepAgents middleware lets you call tools from JavaScript instead of
issuing one `tool_call` per LLM turn. This is a huge speedup when you
need multiple independent queries — one LLM turn becomes one JS
function that fans out into N parallel tool calls.

## Use `Promise.all` for independent queries

When you need multiple results that don't depend on each other:

```javascript
const [costcoTotal, tescoTotal, sainsTotal] = await Promise.all([
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::costco"}),
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::tesco"}),
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::sainsburys"}),
]);
return { costco: costcoTotal[0], tesco: tescoTotal[0], sainsburys: sainsTotal[0] };
```

A 12-month-breakdown by month is 12 independent queries — `Promise.all`
turns 12 LLM turns into 1.

## Chain when one query feeds the next

```javascript
const merchant = (await merchant_resolve("Costco", 1))[0];
const total = await cypher_read_only(
    "MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $id}) " +
    "WHERE t.date >= $start " +
    "RETURN sum(t.amount) AS total",
    { id: merchant.id, start: "2025-01-01" }
);
return { merchant: merchant.canonical_name, total: total[0].total };
```

## Never silent-swallow errors

```javascript
try {
    return await cypher_read_only(query, params);
} catch (err) {
    return { error: String(err) };  // surface the error in the result
}
```

The agent loop sees `{error: ...}` and can re-plan; if you swallow it
into `null`, the next turn has no clue what went wrong.
