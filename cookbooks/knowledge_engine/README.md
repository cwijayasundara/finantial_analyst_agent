# Knowledge Engine Cookbook

Q&A over the compiled graph + wiki, plus operator-grade merchant
consolidation through the action layer.

## Quickstart

```bash
# Plain-English question; agent reads but cannot write
python -m cookbooks.knowledge_engine ask \
  "what was my biggest spending category in April 2025?"

# Direct merge (write — bypasses the agent's read-only constraint)
python -m cookbooks.knowledge_engine merge merchant_amzn merchant_amazon \
  "AMZN is the same brand as Amazon"

# Raw read-only Cypher
python -m cookbooks.knowledge_engine query \
  "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id LIMIT 5"

# Dump a single wiki page
python -m cookbooks.knowledge_engine read merchant_amazon
```

## What the agent can do

| Tool | Read/Write | Surface |
|---|---|---|
| `query_graph(cypher)` | Read-only Cypher | Forbidden keywords rejected; rows capped via `PFH_QA_ROW_LIMIT` |
| `read_wiki_page(page_id)` | Read | Returns frontmatter + body excerpt (≤ 4 KB) |
| `merge_merchants(src, tgt, reason)` | **Write** | Refused by default; only callable when CLI invokes with `allow_writes=True` (the dedicated `merge` subcommand) |

## Privacy contract

The agent uses `build_chat_model()` exactly like every other LLM call in
the system: ollama by default; OpenAI gated by `PFH_ALLOW_REMOTE_LLM=true`;
PII masker + denylist + `assert_no_pii` guard + audit log all apply.

## Skills

- [qa-rubric.md](skills/qa-rubric.md) — citation rules, refusal behaviour
- [cypher-cookbook.md](skills/cypher-cookbook.md) — Kuzu schema + 5 stock queries
