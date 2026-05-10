# `statement-ingester` — cookbook

Deterministic ETL pipeline that turns PDF bank/credit-card statements into:

- `data/ledger.duckdb` — canonical transactions
- `wiki/{merchants,statements,subscriptions}/` — typed wiki pages (audited)
- `graph/kuzu.db` (+ `graph/snapshots/graph.jsonl`) — derived typed graph

Implemented as a LangGraph `StateGraph` with one optional LLM node
(`qwen3.6:35b` via Ollama for merchant categorisation). Every other node
is deterministic.

## Pipeline

```
parse_pdf  →  validate_completeness  →  upsert_ledger
   │                                          │
   └──[failure: report]                       └──[skipped: report]
                                               │
                              ┌────────────────┘
                              ▼
                         (new merchants?)
                          /            \
                     yes /              \ no
                        ▼                ▼
                   categorise       detect_recurring
                        │                │
                        └────────┬───────┘
                                 ▼
                         compile_graph → report → END
```

## Subagent tier (security)

This cookbook is a LangGraph flow rather than a DeepAgents agent, but the
design follows the same three-tier discipline:

| Role | Read | Write |
|---|---|---|
| parse_pdf, validate, recurring | `sources/`, `parsed/` | none direct |
| upsert_ledger, categorise | `parsed/` | DuckDB + wiki/* via Action Types only |
| compile_graph | DuckDB, `wiki/`, `ontology/` | `graph/` |

Direct filesystem writes to `wiki/` are denied; everything goes through
governed Actions in `cookbooks/_shared/ontology/functions/actions.py`.

## Use

```bash
# One file
python -m cookbooks.statement_ingester run sources/savings_stmt/2026_May_Statement.pdf

# Whole tree (idempotent — re-runs are no-ops)
python -m cookbooks.statement_ingester backfill sources/

# Watch mode
python -m cookbooks.statement_ingester watch sources/
```

## Idempotency

| Layer | Mechanism |
|---|---|
| parse | `parsed/<source-subdir>/<source-stem>.md` cache (mirrors `sources/`) |
| upsert | sha256-based short-circuit on `statements`; `INSERT OR IGNORE` on transactions |
| categorise | `data/rules.yaml` lookup before any LLM call |
| recurring | DuckDB candidate set; subscription pages overwritten in place |
| compile | wiki + ontology + ledger fingerprint; skipped when unchanged |

## Steering examples

See [`steering-examples.json`](./steering-examples.json).
