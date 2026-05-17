# Merchant resolution

Free-text merchant names in user questions (e.g. "Costco", "amzn",
"tesco stores") MUST be resolved to canonical IDs before writing
merchant-filtered Cypher. The shape on disk varies (`COSTCO WHSE
#0123`, `COSTCO.COM`, `COSTCO`) but they all hang off one canonical
merchant node.

## When to call merchant_resolve

ALWAYS, when the user names a merchant. Even if they spell it
canonically — there may be aliases the user doesn't know about.

```python
hits = merchant_resolve("Costco", k=5)
# hits = [{"id": "merchant::costco", "canonical_name": "Costco", "score": 4.21}, ...]
```

Then pass `hits[0]["id"]` as a parameter to the Cypher query.

## What to do with multiple hits

If `hits` has more than one with comparable scores (top score within
2x of second-place), tell the user — they may have meant something
specific:

> "I found two candidates: Costco (merchant::costco) and Costco UK
> (merchant::costco_uk). Which one?"

If the top hit dominates (3x+ second-place score), proceed silently
with the top hit and mention it in the answer ("I matched 'Costco'
to merchant::costco").

## What to do with zero hits

Don't guess. Tell the user the merchant wasn't found and ask whether
they want to search a broader pattern (which would require a fuzzy
Cypher MATCH, not the fulltext tool).
