# openclaw — Neo4j + DeepAgent 0.6 Context Engine Upgrade

**Date:** 2026-05-17
**Status:** Design, pending review
**Owner:** cwijay@biz2bricks.ai
**Repo:** `personal_finance_helper` (codename *openclaw*)
**Inspiration repo:** `context_graphs_neo_4j_v1` (LennyGraph)

## 1. Context

openclaw today is a feature-complete personal-finance system (P1–P9 shipped, 443 passing tests) built on a three-store substrate — DuckDB for numerics, Wiki markdown for canonical text, Kuzu for the property graph. The knowledge-engine agent is a hand-rolled 12-iteration tool loop; semantic search is not live; the graph is invisible to the user; categorisation runs through a local-only LLM by default (Ollama / `qwen3.5`).

The LennyGraph repo solves an adjacent problem on a more sophisticated agent stack: DeepAgents 0.6 with Programmatic Tool Calling (PTC) via QuickJS, researcher/critic/synthesizer sub-agents, hybrid semantic + keyword retrieval, an evidence-grounded subgraph UI, and MCP-server exposure. It runs on Neo4j with native vector indexes and APOC.

This spec ports the high-value LennyGraph patterns into openclaw, replaces Kuzu with Neo4j and DuckDB with Postgres (both in local Docker for operational consistency — one mental model, one compose file), trims the Wiki to its prose-only role (memos + decisions), promotes the YAML ontology to the type-system spine, and introduces a hard PII-redaction boundary so GPT-5.4-mini can be used as the reasoning model without personal data ever leaving the host.

## 2. Goals

1. Enable arbitrary natural-language questions over the personal finance graph, with the agent generating Cypher dynamically (no hardcoded query templates).
2. Use GPT-5.4-mini (OpenAI) as the reasoning model, gated behind a deterministic PII-redaction layer that fails closed.
3. Migrate the graph store from Kuzu to Neo4j Community in local Docker.
4. Migrate the numerics store from DuckDB to Postgres 16 in local Docker, so all infra runs under one compose file.
5. Trim the Wiki to its prose-only role (memos + decisions); move structured entities (merchants, statements, budgets, goals) out of Wiki into Postgres + Neo4j.
6. Promote the YAML ontology (`cookbooks/_shared/ontology/*.yaml`) to the single source of truth for the type system; generate `init.cypher`, Pydantic models, and the agent's schema prompt from it.
7. Surface the evidence subgraph for every answer in the existing Next.js dashboard, mirroring LennyGraph's UX.
8. Reconcile every numeric answer against Postgres before showing it to the user.
9. Expose the engine as an MCP server so questions can be asked from inside Claude Code.

## 3. Non-goals

- Multi-tenant isolation, auth, or RBAC. Single-user local deployment.
- Real-time alerting, Plaid/Stripe integration, mobile app.
- Rewriting the LangGraph pipelines (statement_ingester, monthly_analyst, advisor, eval). They remain in place; only their storage targets change (DuckDB → Postgres, Kuzu → Neo4j) and their LLM calls go through the redactor.
- Eliminating Wiki entirely — memos and decisions stay there because git-tracked markdown is the right substrate for human-authored prose and immutable audit logs.
- Eliminating the ontology — it's promoted, not deleted; it's the spine the other stores derive from.
- Streaming ingestion. PDFs continue to be batch-ingested.

## 4. Architecture overview

```
ontology/*.yaml ──generates──► init.cypher (Neo4j schema)
       │                       Pydantic models (validation)
       │                       schema prompt (for the agent)
       │
       ▼
 ┌──────────────────────────────────────────────────┐
 │  Next.js dashboard (127.0.0.1, CSP-locked)       │
 │   /qa   /memos   /merchants   /graph   /decisions│
 └────────────┬────────────────────────┬────────────┘
              │ SSE answers + evidence │ /graph/* REST
              │                        │
  ┌───────────▼────────────┐  ┌────────▼────────────┐
  │  FastAPI /chat (SSE)   │  │  FastAPI /graph/... │
  └───────────┬────────────┘  └────────┬────────────┘
              │                        │
              │  DeepAgent 0.6         │
              │  (PTC + sub-agents)    │
              │                        │
┌─────────────▼────────────┐           │
│ Redacting LLM proxy      │           │
│ (PII tokenize / restore) │           │
└─────────────┬────────────┘           │
              │ redacted prompt        │
       ┌──────▼──────┐                 │
       │ GPT-5.4-mini│                 │
       │  (OpenAI)   │                 │
       └─────────────┘                 │
                                       │
┌──────────────────────────────────────┼─────────────────────────┐
│             tools (via PTC)          │                         │
│ cypher_read_only │ sql_read_only │ merchant_resolve │ ...      │
└──────┬───────────────┬──────────────────┬──────────────────────┘
       │               │                  │
┌──────▼─────┐  ┌──────▼──────┐   ┌──────▼─────────┐  ┌─────────────┐
│  Neo4j 5   │  │ Postgres 16 │   │   Wiki         │  │  Local      │
│  (Docker)  │  │  (Docker)   │   │  memos +       │  │  embeddings │
│  APOC + vec│  │  numerics   │   │  decisions     │  │  (MiniLM)   │
│  + fulltext│  │  oracle     │   │  (markdown +   │  │  no PII     │
│            │  │  read-only  │   │   git)         │  │  over wire  │
│            │  │  for critic │   │                │  │             │
└────────────┘  └─────────────┘   └────────────────┘  └─────────────┘
```

Three persistent stores, three roles — each with one responsibility:

- **Postgres 16 (Docker)** — numerics source of truth. Transactions, merchants, categories, accounts, statements, budgets, goals, patterns. Same Docker workflow as Neo4j (one compose file, both bound to 127.0.0.1). Ingestion, monthly_analyst, and the critic write/read here.
- **Wiki (markdown on disk)** — prose source of truth, now **scoped to memos + decisions only**. Long-form monthly analysis and the append-only audit log. Human-editable, git-tracked. Merchant/Statement/Budget wiki pages are dropped — those were structured-data mirrors, owned by Postgres now.
- **Neo4j 5 (Docker)** — derived graph of entities and relationships, plus vector + full-text indexes for retrieval. Compiled from Postgres + Wiki via `compile_neo4j.py`; recompilable from scratch at any time.

The **ontology YAML** is not a store — it's the *schema spine*. It generates the Neo4j `init.cypher`, the Pydantic models that validate writes, and the schema prompt the agent reads. Single source of truth for types and relations.

## 5. PII redaction layer (Tier 0 — gating)

All other work depends on this. No remote LLM call may be made without going through this proxy.

### 5.1 Implementation site

Replaces `cookbooks/_shared/llm.py::_AuditingChat` with `_RedactingChat`. Same proxy pattern (`langchain_core.runnables.Runnable` wrapper), new responsibility. Audit logging is retained and extended.

### 5.2 PII categories and detectors

| Category | Detector | Notes |
|---|---|---|
| Person names | Presidio + spaCy `en_core_web_lg` | Plus exact match against `accounts.holder_name` (Postgres) |
| UK sort codes | regex `\b\d{2}-\d{2}-\d{2}\b` | High precision |
| Account numbers | regex `\b\d{8}\b` within 30 chars of "account"/"acct"/"a/c" | Confirmed by context |
| Card numbers | Luhn-checked 13–19 digit runs | |
| Addresses | Presidio `LOCATION` + UK postcode regex `[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}` | |
| DOB | Presidio `DATE_TIME` filtered by year-of-birth heuristic | |
| Phone | Presidio `PHONE_NUMBER` (UK + intl recognizers) | |
| Email | Presidio `EMAIL_ADDRESS` | |
| IBAN | Presidio `IBAN_CODE` | |

Merchant names are **not** PII and are not redacted. Amounts and dates are not PII and are not redacted.

### 5.3 Tokenization

Deterministic per session, in-memory only:

```
"John Smith, sort 12-34-56, acct 87654321, spent £42 at Costco"
→
"<<PERSON_001>>, sort <<SORT_001>>, acct <<ACCT_001>>, spent £42 at Costco"
```

Reverse map kept in a session-scoped dict. Never persisted, never logged in plaintext. Same PII string in the same session → same token (preserves co-reference for the LLM).

### 5.4 De-tokenization

Applied to LLM output before returning to the agent loop. Unknown tokens (e.g. hallucinated `<<PERSON_999>>`) are stripped, not passed through.

### 5.5 Audit

`data/openai_audit.jsonl` records per call:

```json
{
  "ts": "2026-05-17T10:00:00Z",
  "session_id": "...",
  "model": "gpt-5.4-mini",
  "prompt_redacted": "...",
  "prompt_sha256": "<hash of original>",
  "response_redacted": "...",
  "tokens_in": 1234, "tokens_out": 567
}
```

Hash of original (not plaintext) lets a future audit prove redaction was applied without storing the leak.

### 5.6 Tripwire (fail-closed)

After redaction and immediately before the HTTP call, the payload is re-scanned with a tighter regex set (sort codes, 8+ digit runs, UK postcodes, IBAN). Any hit → raise `PiiLeakError`, do not send. This catches detector misses and saves the user from silent leaks.

### 5.7 Tests

`tests/test_pii_redaction.py` with synthetic fixtures covering every category. Includes adversarial cases: PII split across sentences, PII in JSON values, PII inside Cypher string literals returned from `cypher_read_only`. The tripwire must catch every fixture *even with all detectors disabled* (i.e. tripwire alone is sufficient for the regex-detectable categories).

### 5.8 Config

`.env`:
```
PFH_LLM_PROVIDER=openai
PFH_LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=...
PFH_REDACTION_REQUIRED=true   # fails closed if redactor not configured
PFH_REDACTION_TRIPWIRE=true   # disable only in tests
```

## 6. Infrastructure migration (Tier 1.1)

Postgres and Neo4j ship together in a single `docker/docker-compose.yml` — one mental model, one `docker compose up` to bring infra online. The ontology generates the schemas for both.

### 6.1 Docker compose

`docker/docker-compose.yml`:

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    container_name: openclaw-neo4j
    ports:
      - "127.0.0.1:7474:7474"
      - "127.0.0.1:7687:7687"
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: apoc.*
      NEO4J_dbms_memory_pagecache_size: 1G
      NEO4J_dbms_memory_heap_max__size: 2G
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: openclaw-postgres
    ports:
      - "127.0.0.1:5432:5432"
    environment:
      POSTGRES_USER: openclaw
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: openclaw
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openclaw -d openclaw"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  neo4j_data:
  neo4j_logs:
  postgres_data:
```

Both services bind to loopback only. Neo4j Community gives us APOC + native vector indexes (5.11+) + full-text indexes. Postgres 16 gives us JSONB, BRIN indexes for time-series, mature ACID, and `pg_dump` for backup. No extension required — `pgvector` and `pg_trgm` are not needed (vectors live in Neo4j, fuzzy text search lives in Neo4j's full-text index).

### 6.2 Postgres schema and migration from DuckDB

Schema mirrors the existing DuckDB tables (`accounts`, `statements`, `transactions`, `merchants`, `categories`, `patterns`, `budgets`, `goals`) with Postgres-native idioms:

- `BIGSERIAL` / `UUID` for surrogate keys; `TEXT` for canonical IDs
- `NUMERIC(14, 2)` for `amount_gbp` (never `FLOAT`)
- `DATE` for transaction dates; `TIMESTAMPTZ` for `created_at` / `updated_at`
- `JSONB` for parser output blobs and statement metadata (replaces DuckDB `JSON`)
- Foreign keys enforced (`ON DELETE RESTRICT` for transactional integrity)
- `BRIN` index on `transactions(date)` (cheap, perfect for time-series at this scale)
- `BTREE` indexes on `transactions(merchant_id)`, `(category_id)`, `(account_id, date)`
- Optional `pgcrypto` column-level encryption for `accounts.holder_name` and `accounts.account_number` — defense-in-depth even though the DB is local

**Migrations** via Alembic in `db/postgres/migrations/`. Initial migration ports DuckDB DDL 1:1; subsequent migrations are additive.

**Data migration** from DuckDB: since user is happy to repopulate, the simplest path is:
1. Stand up Postgres
2. Run Alembic to baseline
3. Re-run `statement_ingester backfill` against Postgres (re-parses PDFs)
4. Verify row counts match the old `data/ledger.duckdb` (one-time sanity check via a small `migrate_check.py` script that opens both)
5. Delete `data/ledger.duckdb`

No data-import scripts needed; PDFs are the ground truth and re-ingestion is idempotent (statements keyed on SHA-256).

### 6.3 Neo4j schema

`db/neo4j/init.cypher` is **generated from the ontology** (see §6.7), not hand-written. Node labels and ID schemes the ontology defines:

| Label | ID | Embedding field | Notes |
|---|---|---|---|
| `Account` | `account::<sha256-of-number>` | — | `holder` is PII, never exposed |
| `Statement` | `statement::<sha256-of-pdf>` | — | `source_pdf` stored as relative path |
| `Transaction` | `tx::<statement-id>::<row>` | `clean_description` (optional) | Numeric truth lives in Postgres |
| `Merchant` | `merchant::<canonical-slug>` | `canonical_name + aliases` | |
| `Category` | `category::<slug>` | — | Hierarchical via `PARENT_OF` |
| `Memo` | `memo::<period>` | per-paragraph chunks | `wiki_path` property points to `wiki/memos/<file>.md` — content not duplicated |
| `Decision` | `decision::<uuid>` | — | Audit boundary; `wiki_path` points to `wiki/decisions/<file>.md` |
| `Concept` | `concept::<slug>` | `name + description` | E.g. "subscription bloat" |

Relationships: `HAS_STATEMENT`, `HAS_TRANSACTION`, `AT_MERCHANT`, `IN_CATEGORY`, `PARENT_OF`, `MENTIONS`, `DERIVED_FROM`, `ABOUT`, `RECURRING_PATTERN`.

`Memo` and `Decision` nodes carry a `wiki_path` property only — the **content of memos and decisions lives in the Wiki markdown files**, not in Neo4j. This keeps Neo4j fast (no large text blobs) and makes the wiki the single editable surface for prose.

Indexes:
- Unique constraint on `id` for every label
- Vector index on `Merchant.embedding` (384-d cosine, MiniLM)
- Vector index on `Memo.embedding` (384-d cosine) — embeds the *content* read from `wiki_path` at compile time, not stored
- Vector index on `Concept.embedding` (384-d cosine)
- Vector index on `Transaction.embedding` (384-d cosine)
- Full-text index over `Merchant.canonical_name + aliases`, `Concept.name + description`

A singleton `(:Meta {id: 'schema'})` node carries `schema_version` (int), `embedding_model` (string), and `embedding_dim` (int). `init.cypher` writes the current values; `compile_neo4j.py` reads them at startup and refuses to write if they don't match the running config — a mismatch requires the explicit drop+rebuild script (`db/neo4j/drop_and_rebuild.cypher`) documented in the runbook.

### 6.4 Compile step (Postgres + Wiki → Neo4j)

`cookbooks/_shared/compile_neo4j.py` replaces `compile_graph.py`. Reads from Postgres (entities + relationships) and Wiki (memo/decision content for embedding), writes to Neo4j via the official `neo4j` Python driver with MERGE-on-id, mirroring LennyGraph's `/backend/ingestion/loader.py` pattern:

```python
UPSERT_MERCHANT = """
CALL apoc.merge.node(
  ['Merchant'], {id: $id},
  {canonical_name: $name, aliases: $aliases, embedding: $emb},
  {updated_at: timestamp()}
) YIELD node RETURN node
"""
```

Idempotent. Fingerprint-skip (existing pattern): if `MAX(merchants.updated_at)` and `MAX(transactions.updated_at)` from Postgres + `mtime` of wiki memo files haven't moved since the last compile, skip.

### 6.5 Repopulation runbook

Documented in `docs/runbook-rebuild-graph.md`:

```
docker compose -f docker/docker-compose.yml up -d
uv run alembic -c db/postgres/alembic.ini upgrade head
uv run python -m cookbooks._shared.init_neo4j           # runs generated init.cypher
uv run python -m cookbooks.statement_ingester backfill  # PDFs → Postgres
uv run python -m cookbooks._shared.compile_neo4j --full # Postgres + Wiki → Neo4j
```

Wiki is unaffected; both Postgres and Neo4j are rebuildable from PDFs + Wiki alone.

### 6.6 Removal of Kuzu and DuckDB

After Postgres + Neo4j are green end-to-end:

**Kuzu:**
- Delete `graph/kuzu.db`
- Remove `kuzu` from `pyproject.toml`
- Remove `cookbooks/_shared/compile_graph.py`

**DuckDB:**
- Delete `data/ledger.duckdb`
- Remove `duckdb` from `pyproject.toml`
- Replace all `duckdb.connect(...)` sites in `cookbooks/_shared/db.py` and the LangGraph pipelines with `psycopg.connect(...)` via a shared connection-pool helper
- Keep the `_shared/db.py` interface stable so pipeline code changes are limited to imports and SQL dialect tweaks (e.g. `DATE_TRUNC` works in both; `QUALIFY` is DuckDB-only and needs rewriting)

**Wiki trim:**
- Move merchant/statement/budget/goal wiki pages into Postgres rows (one-time `migrate_wiki_to_postgres.py` script — idempotent, dry-run by default)
- Delete `wiki/merchants/`, `wiki/statements/`, `wiki/budgets/`, `wiki/goals/` after verification
- Keep `wiki/memos/` and `wiki/decisions/` — these are now the entire wiki scope

**Docs:**
- Update `docs/architecture.md` to reflect the trimmed three-store split

### 6.7 Ontology as schema spine

The YAML ontology at `cookbooks/_shared/ontology/*.yaml` (already 11 ObjectTypes + 17 LinkTypes + 10 ActionTypes) is promoted to **the single source of truth for the type system**. Three generators consume it:

| Generator | Output | When it runs |
|---|---|---|
| `cookbooks/_shared/ontology/gen_init_cypher.py` | `db/neo4j/init.cypher` (constraints, indexes, vector indexes, full-text indexes) | Manual; output committed to git so Neo4j init is reviewable |
| `cookbooks/_shared/ontology/gen_pydantic.py` | `cookbooks/_shared/models/_generated.py` (Pydantic v2 models) | Pre-commit hook; existing pattern formalised |
| `cookbooks/_shared/ontology/gen_schema_prompt.py` | `cookbooks/_shared/skills/_generated_schema.md` (schema block for agent system prompt) | Pre-commit hook |

The agent's schema prompt is read from `_generated_schema.md`, **not** from `CALL db.schema.visualization()`. Three reasons:

1. **Stability** — runtime introspection drifts if anyone manually edits the graph via Neo4j Browser; the ontology can't
2. **Reviewability** — the schema prompt is committed to git; changes are visible in PRs
3. **ActionTypes have no graph mapping** — recommendations like "merge merchants" or "increase budget" need to be in the agent's prompt anyway, and the ontology is the only place they live

Postgres DDL is **also** generated from the ontology where it overlaps (Account, Statement, Transaction, Merchant, Category, Budget, Goal, Pattern). Alembic migrations are hand-authored on top of the generated baseline.

CI check: a `tests/test_ontology_consistency.py` verifies that the generated artefacts match what's committed (`git diff --exit-code` after regenerate). Forces ontology edits and generator output to land in the same PR.

## 7. Cypher generation (Tier 1.2)

No hardcoded query templates. The agent writes Cypher per-question.

### 7.1 The `cypher_read_only` tool

Ported from LennyGraph's `/backend/context_graph/tools.py::cypher_read_only`, with stricter guards:

```python
@tool
def cypher_read_only(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query. Returns up to 1000 rows."""
    _reject_write_keywords(query)                    # token-level, not substring
    plan = session.run(f"EXPLAIN {query}", params).consume().plan
    if plan.arguments.get("DbHits", 0) > MAX_DB_HITS:
        raise CypherTooExpensive(...)
    if not _has_limit_clause(query):
        query = f"{query}\nLIMIT 1000"
    with session.begin_transaction(timeout=5) as tx:
        return [r.data() for r in tx.run(query, params or {})]
```

Audit log: every query + row count + duration.

### 7.2 The `cypher_explain` tool

Same guards but returns the plan, not results. Lets the agent validate before running expensive queries.

### 7.3 Schema in the prompt

System prompt for the agent includes a `SCHEMA:` block generated from the ontology (see §6.7), committed to git as `cookbooks/_shared/skills/_generated_schema.md`:

```
SCHEMA:
(Account)-[:HAS_STATEMENT]->(Statement)-[:HAS_TRANSACTION]->(Transaction)
(Transaction)-[:AT_MERCHANT]->(Merchant)
(Transaction)-[:IN_CATEGORY]->(Category)
(Category)-[:PARENT_OF]->(Category)
(Merchant)-[:RECURRING_PATTERN]->(Pattern)
(Memo)-[:MENTIONS]->(Merchant|Category|Transaction)
(Decision)-[:DERIVED_FROM]->(Transaction|Memo|...)
(Concept)-[:ABOUT]->(Merchant|Category)

PROPERTIES (key fields):
  Transaction { id, date, amount_gbp, raw_description, clean_description }
  Merchant    { id, canonical_name, aliases[], embedding }
  Category    { id, name, parent_id }
  ...
```

Generated from the ontology YAML, not from runtime Neo4j introspection — keeps the prompt stable, reviewable in git, and immune to ad-hoc graph edits.

### 7.4 Few-shot exemplars

5–10 example (question, Cypher) pairs across query shapes (merchant×month, category×month, top-N, YoY, path). These *teach the pattern* without constraining the question. Stored in `cookbooks/_shared/skills/cypher-generation-style.md` (loaded as a DeepAgents skill).

### 7.5 Merchant resolution

Free-text merchant names in the user's question are resolved before Cypher runs, via the `merchant_resolve(query)` tool:

```python
@tool
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical IDs."""
    # 1. Embedding search over Merchant.embedding
    # 2. Full-text search over canonical_name + aliases
    # 3. RRF blend, return top-k {id, canonical_name, score}
```

The agent calls this before any merchant-filtered Cypher. Mirrors LennyGraph's `hybrid_search` (`/backend/context_graph/api.py`).

## 8. DeepAgents 0.6 + sub-agents (Tier 1.3)

### 8.1 Rewrite of `knowledge_engine/agent.py`

The hand-rolled 12-iteration loop is replaced with:

```python
from deepagents import create_deep_agent
from deepagents.middleware import CodeInterpreterMiddleware

agent = create_deep_agent(
    model=redacting_chat_model,
    tools=[
        cypher_read_only, cypher_explain,
        sql_read_only,                    # Postgres escape hatch (same guard pattern)
        merchant_resolve, evidence_for,
        read_wiki_page, postgres_total_reconcile,
    ],
    system_prompt=KNOWLEDGE_AGENT_PROMPT,
    middleware=[CodeInterpreterMiddleware(ptc=ALL_TOOLS)],
    subagents=[RESEARCHER, SYNTHESIZER, CRITIC],
    skills=[
        "cookbooks/_shared/skills/cypher-generation-style.md",
        "cookbooks/_shared/skills/merchant-resolution.md",
        "cookbooks/_shared/skills/citation-format.md",
        "cookbooks/_shared/skills/pii-redaction.md",
        "cookbooks/_shared/skills/ptc-patterns.md",
    ],
    checkpointer=InMemorySaver(),
)
```

PTC means one LLM turn → QuickJS orchestrates multiple tool calls (e.g. `Promise.all` over 12 months of queries) → joined result returned to the model.

### 8.2 Sub-agents

| Sub-agent | Tools | Purpose |
|---|---|---|
| **researcher** | `merchant_resolve`, `cypher_read_only`, `cypher_explain`, `sql_read_only`, `evidence_for`, `read_wiki_page` | Resolve entities + dates from the question; run discovery queries against Neo4j (graph shape) or Postgres (raw numerics); return raw findings + candidate Cypher. |
| **synthesizer** | `cypher_read_only`, `sql_read_only`, `read_wiki_page` | Compose the final answer with inline citations `[stmt::id row N]` and the evidence subgraph IDs. |
| **critic** | `postgres_total_reconcile`, `cypher_read_only` | Re-run synthesizer's totals as direct Postgres SQL; reject answer if mismatch > tolerance (0.01 GBP). |

The critic is the safety net for the remote LLM: Postgres is the ground-truth oracle, the LLM never sees raw amounts in a way that lets it hallucinate aggregates without verification. `postgres_total_reconcile(claim)` parses the synthesizer's structured claim (entity + date range + aggregate type), issues the equivalent direct SQL via a small connection pool, and returns `{matches: bool, expected, found, drift}`.

### 8.3 Profiles

`cookbooks/_shared/profiles.py` registers a `HarnessProfile` for `gpt-5.4-mini` with suffix guidance for finance-domain Cypher style and citation format.

## 9. Graph viz UI (Tier 1.4)

### 9.1 Backend endpoints

Added to `cookbooks/api/` (FastAPI), mirroring LennyGraph's `/backend/retrieval/server.py`:

```
GET  /graph/node/{id}
GET  /graph/neighbors/{id}?depth=2&rel_types=...
GET  /graph/path?from={id}&to={id}&max_depth=4
GET  /graph/evidence/{answer_id}
POST /chat  (SSE — already exists; payload extended to include evidence subgraph IDs)
```

`/graph/evidence/{answer_id}` returns the subgraph of nodes the synthesizer cited in its answer.

### 9.2 Frontend

`web/` (Next.js, P6) gains:

- `react-force-graph-2d` (MIT) on the `/qa` page
- Side panel: when an SSE answer completes, the response payload includes `evidence_ids: [...]`; the panel fetches `/graph/evidence/{answer_id}` and renders the subgraph
- Click a node → drill into the merchant/transaction/memo page
- Existing `/graph` page is repurposed to a free-explore view (load by ID, expand neighbours)

CSP and 127.0.0.1-only binding preserved.

### 9.3 What gets shown

For an answer like "you spent £342 at Costco in March, mostly groceries":
- The `Merchant{Costco}` node
- The 7 `Transaction` nodes contributing to the total
- The `Category{groceries}` they're linked to
- The `Statement` they came from (clickable → opens the Docling-parsed Markdown)

## 10. MCP server (Tier 1.5)

`cookbooks/api/mcp_server.py`, mirroring `/backend/context_graph/mcp_server.py` in LennyGraph. Stdio transport. Tools exposed are **general-purpose verbs**, not frozen-question templates — each one takes parameters that the caller fills freely, and the underlying implementation generates Cypher dynamically:

- `cypher_read_only(query, params)` — primary escape hatch; same guards as §7.1
- `merchant_resolve(query)` — hybrid search over `Merchant`
- `evidence_for(claim, k)` — top-k transactions/memos supporting a claim
- `neighbors(node_id, depth, rel_types)` — local subgraph
- `recurring_charges()` — surfaces the `RECURRING_PATTERN` edges

No verb is question-specific (no `costco_breakdown`, no `monthly_groceries`). The MCP client (Claude Code) composes them.

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "openclaw": {
      "command": "uv",
      "args": ["run", "python", "-m", "cookbooks.api.mcp_server"],
      "cwd": "/Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper"
    }
  }
}
```

Same redactor + same audit. Questions can now be asked from any Claude Code session.

## 11. Tier 2 — quality & ergonomics

### 11.1 Skill files

`cookbooks/_shared/skills/`:

- `cypher-generation-style.md` — schema + few-shots, style rules (use parameters, prefer `MATCH (n:Label)` over generic patterns)
- `merchant-resolution.md` — when to call `merchant_resolve` (always, before merchant-filtered Cypher)
- `citation-format.md` — required `[stmt::id row N]` shape, link to source PDF path
- `pii-redaction.md` — what the agent should and should not say (e.g. never echo a sort code in a clarification question)
- `ptc-patterns.md` — when to use `Promise.all`, when to chain

### 11.2 Enforced citations

Synthesizer prompt requires every numeric claim to carry `[stmt::id row N]`. Critic rejects answers without them. Citations resolve to file paths under `parsed/<mirror>/<stmt>.md` and to the Docling-parsed page.

### 11.3 Pre-baked queries as warm-cache only

Top-N common question shapes are kept as **examples in the few-shot block**, not as required code paths. The agent is free to write fresh Cypher.

## 12. Tier 3 — longer-term

Not in this slice, but the design leaves room:

- Merchant resolution feedback loop (Decision-driven updates to `data/rules.yaml`)
- Concept layer with embeddings ("subscription bloat", "Costco run pattern") allowing semantic queries to land on derived concepts
- Outbound network egress allow-list at the OS level as defense-in-depth

## 13. Model split

| Concern | Model | Where it runs | Why |
|---|---|---|---|
| Reasoning (all agents) | GPT-5.4-mini | OpenAI, post-redaction | Quality + latency |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-d) | Local Python | No PII over wire; batch ingestion has no API cost |
| NER for redaction | spaCy `en_core_web_lg` + Presidio | Local | Required for the redactor |
| Categorisation | GPT-5.4-mini (post-redaction) | OpenAI | Was Ollama; can move to OpenAI now redactor exists |

Ollama is no longer required. Removing the dependency is in scope but optional — current `PFH_LLM_PROVIDER=ollama` paths remain functional for offline-only use.

## 14. Implementation order

PRs land in this order. Each is independently reviewable.

1. **Tier 0 — PII redactor + tripwire + tests.** No remote call goes live until this is green. Synthetic fixtures cover every category. Tripwire alone must be sufficient for regex-detectable categories.
2. **Ontology generators + CI check.** Add `gen_init_cypher.py`, `gen_pydantic.py`, `gen_schema_prompt.py` and the consistency test. Generated artefacts committed. No behaviour change yet — but unblocks every downstream PR by making the schema source-of-truth explicit.
3. **Postgres in Docker + Alembic baseline + `_shared/db.py` swap from DuckDB to psycopg.** Repopulate via `statement_ingester backfill`. DuckDB kept in parallel for one PR cycle behind a `PFH_LEDGER_BACKEND` env (`duckdb`|`postgres`) so the existing test suite can validate equivalence, then removed.
4. **Neo4j in Docker + generated `init.cypher` + `compile_neo4j.py`.** Source: Postgres + Wiki. Kuzu kept in parallel for one PR cycle as a sanity check, then removed.
5. **`cypher_read_only` + `cypher_explain` + `sql_read_only` + guards + schema-in-prompt.** Wired into the existing knowledge-engine loop first (before DeepAgents rewrite) to validate guards in isolation.
6. **DeepAgents 0.6 rewrite + researcher/synthesizer/critic + PTC + skills.** Replaces the hand-rolled loop.
7. **`/graph/*` endpoints + react-force-graph-2d on `/qa`.** Evidence subgraph rendered alongside each answer.
8. **MCP server.** Smallest PR; copy-port from LennyGraph.
9. **Wiki trim + Kuzu removal + DuckDB removal + docs/architecture.md update.** Bundled cleanup.

## 15. Testing strategy

- **PII redaction:** `tests/test_pii_redaction.py` — synthetic statements with every PII category, every detector toggled on/off independently. Tripwire fixtures must pass even with detectors fully disabled.
- **Ontology consistency:** `tests/test_ontology_consistency.py` — regenerate `init.cypher`, Pydantic models, schema prompt; `git diff --exit-code` against committed artefacts. Forces every ontology change to ship with its generated outputs.
- **Postgres schema:** `tests/test_postgres_schema.py` — testcontainers-python `postgres:16-alpine`; Alembic upgrade/downgrade round-trips; FK enforcement; idempotent re-ingestion.
- **DuckDB→Postgres equivalence:** `tests/test_ledger_backends.py` — runs the existing pipeline tests against both backends behind `PFH_LEDGER_BACKEND` for one PR cycle; deleted after DuckDB removal.
- **Cypher guards:** `tests/test_cypher_read_only.py` — write keywords (MERGE, CREATE, DELETE, SET, REMOVE, DROP, CALL apoc writes, periodic commit), oversized plans, missing LIMIT, timeout.
- **SQL guards:** `tests/test_sql_read_only.py` — same shape for Postgres (rejects INSERT/UPDATE/DELETE/DDL/COPY, uses a `SET TRANSACTION READ ONLY` session).
- **Critic reconciliation:** `tests/test_critic.py` — golden answers with intentional total drift; critic must reject.
- **End-to-end agent eval:** extend the existing P9 YAML eval framework with question/answer pairs that exercise the full PTC loop. Snapshot Cypher + evidence subgraph in addition to the prose answer.
- **Graph compile:** `tests/test_compile_neo4j.py` — compile against testcontainers Postgres + Neo4j, assert node/edge counts match the Postgres+Wiki source.

## 16. Risks & mitigations

| Risk | Mitigation |
|---|---|
| PII detector misses a category | Tripwire fail-closed before HTTP send; audit log includes hash of original for forensic verification |
| Agent generates expensive Cypher / SQL | EXPLAIN + dbHits/cost cap + 5s timeout + implicit LIMIT 1000; `SET TRANSACTION READ ONLY` for SQL |
| Hallucinated totals reach the user | Critic re-runs aggregates as direct Postgres SQL; rejects > 0.01 GBP drift |
| Migration loses data | Wiki + PDFs are untouched and authoritative; both Postgres and Neo4j are recompilable from scratch via `statement_ingester backfill` + `compile_neo4j` |
| Postgres container down → app fully offline | `restart: unless-stopped`; healthcheck in compose; clear error surfaced to user ("infra down: run `docker compose up`"); CLI tools that read PDFs directly remain functional |
| Neo4j container down → only Q&A offline | Same restart + healthcheck; pipelines that only touch Postgres (ingestion, monthly_analyst) continue to work |
| OpenAI outage | Local Ollama path retained; `PFH_LLM_PROVIDER` env switch |
| Vector index dimension change | `(:Meta {id:'schema'})` version on init; explicit drop+rebuild script documented in runbook |
| Docker services exposed to network | Both services bind 127.0.0.1 only; documented and tested |
| Ontology drift from runtime state | CI consistency test (§15) blocks PRs that regenerate to a different output; runtime introspection never trusted over the ontology |

## 17. Open questions for review

None at the spec level — the proposal answers Tier 0/1, defers Tier 3, and explicitly removes hardcoded templates. Implementation-level decisions (e.g. which Presidio recognizers exactly, exact MiniLM variant) are deferred to the writing-plans phase.
