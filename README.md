# Personal Finance Helper (codename *openclaw*)

Privacy-first, locally-hosted personal finance analyser, advisor, and
budget manager. Ingests PDF bank and credit-card statements, normalises
into a typed graph + ledger, and exposes a multi-cookbook agentic
surface for monthly memos, Q&A, budget tracking, and actionable
recommendations.

**Status:** P1–P9 shipped; openclaw upgrade complete (Plans 1-4 +
Tier-3 vector merchant resolve). **584 unit tests passing.**
Architecture lives in [`docs/architecture.md`](docs/architecture.md);
spec at [`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md`](docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md).

## What it does

| Cookbook / module | What it produces |
|---|---|
| [`statement_ingester`](cookbooks/statement_ingester/) | DuckDB ledger + Wiki pages from your PDFs |
| [`monthly_analyst`](cookbooks/monthly_analyst/) | One Markdown memo per period with rollups + anomalies + budget variance + forecast + goals |
| [`knowledge_engine`](cookbooks/knowledge_engine/) | Q&A agent over the ledger + graph + wiki, with `[stmt::id row N]` citations |
| [`advisor`](cookbooks/advisor/) | Recommendations (cancel sub, adjust budget, investigate anomaly, forecast overshoot, goal off-track, credit payoff) + concept-review queue |
| [`api`](cookbooks/api/) + [`web`](web/) | FastAPI + Next.js dashboard (local-only, 127.0.0.1) with subgraph viz |
| [`api/mcp_server.py`](cookbooks/api/mcp_server.py) | Model Context Protocol server — exposes 5 read-only tools to Claude Code |
| `_shared/compile_neo4j.py` | Compiles ledger + wiki → Neo4j (with vector-indexed merchant embeddings) |

## Quick start — ingest and analyse your statements

This is the end-to-end path from "I have PDF statements" to "I can ask
natural-language questions about my spending".

### 1. One-time install

```bash
# Clone and install
git clone <repo-url> && cd personal_finance_helper

# Python deps via uv
uv sync --extra dev --extra remote --extra web
uv pip install -e .

# Local NLP models (~80 MB + ~580 MB; one-time)
uv run python -m spacy download en_core_web_lg
# sentence-transformers MiniLM-L6-v2 downloads on first use to
# ~/.cache/huggingface

# Web frontend
cd web && pnpm install && cd ..

# Copy the env template — defaults to fully-local Ollama
cp .env.example .env
```

### 2. Choose your LLM path

| Path | When to pick | What to set in `.env` |
|---|---|---|
| **Local Ollama** (default — zero external network) | Slower / weaker than GPT but truly air-gapped | `PFH_LLM_MODEL=ollama:qwen3.5:latest` (default). Also `ollama pull qwen3.5:latest nomic-embed-text` once. |
| **OpenAI GPT-5.4-mini** (recommended — faster, better, with hard PII redaction) | You want quality but **never** want PII to leave the host | `PFH_ALLOW_REMOTE_LLM=true`, `PFH_LLM_MODEL=openai:gpt-5.4-mini`, `OPENAI_API_KEY=sk-...`, optionally `PFH_PII_DENYLIST=YOURNAME,FAMILYNAME` |

The OpenAI path goes through the `_RedactingChat` proxy
(`cookbooks/_shared/llm.py`): every outgoing message is tokenized
(`<<PERSON_001>>`, `<<SORT_001>>`...), `assert_no_pii()` fails closed
if anything PII-shaped survived, and responses are detokenized before
you see them. The audit log at `data/openai_audit.jsonl` records the
hash of every original prompt (never the plaintext).

### 3. Bring up infra (Postgres + Neo4j in Docker)

```bash
# Set passwords for the containers
echo "POSTGRES_PASSWORD=local-dev" > docker/.env
echo "NEO4J_PASSWORD=local-dev" >> docker/.env

# Start both
docker compose -f docker/docker-compose.yml up -d

# Initialise Postgres schema (idempotent)
export PFH_PG_URL=postgresql+psycopg://openclaw:local-dev@127.0.0.1:5432/openclaw
uv run alembic -c db/postgres/alembic.ini upgrade head

# Initialise Neo4j schema (idempotent)
export PFH_NEO4J_URL=bolt://127.0.0.1:7687
export PFH_NEO4J_PASSWORD=local-dev
uv run python -m cookbooks._shared.init_neo4j
```

If you'd rather start with DuckDB only (no Docker), skip this section
— DuckDB is the default ledger. You can switch later by setting
`PFH_LEDGER_BACKEND=postgres`.

### 4. Drop your PDFs and ingest

```bash
# Organise PDFs under sources/<account-folder>/
#   sources/savings_stmt/Statement_1234_Jan-26.pdf
#   sources/crdit_stmt/Statement_5678_Jan-26.pdf

# Backfill (idempotent — re-runs skip on SHA-256 match)
uv run python -m cookbooks.statement_ingester backfill sources/
```

What this does, per PDF:
1. Parse via Docling (with MarkItDown fallback) → Markdown in `parsed/`
2. Validate + upsert rows into the ledger (DuckDB or Postgres)
3. LLM-categorise new merchants (uses your configured chat model;
   masked / redacted as needed)
4. Detect recurring patterns (subscriptions)
5. Write Decision pages to `wiki/decisions/` (audit boundary)

### 5. Compile the graph for fast lookups

```bash
# Postgres + Wiki → Neo4j (idempotent; fingerprint-skip)
uv run python -m cookbooks._shared.compile_neo4j
```

This populates Account / Statement / Merchant / Category / Transaction
nodes plus their relationships, and **embeds every merchant's canonical
name + aliases** into a 384-d vector index. Result: free-text merchant
lookups ("Costco", "AMZN MKTP", typos) all resolve to the same canonical
node via the hybrid full-text + vector path in `merchant_resolve`.

### 6. Generate analyses

```bash
# Monthly memo (rollups + anomalies + budget variance + forecast + goals)
uv run python -m cookbooks.monthly_analyst analyse 2026-04
# or a range:
uv run python -m cookbooks.monthly_analyst backfill-memos 2026-01 2026-04
open wiki/memos/memo_2026_04.md

# Actionable recommendations
uv run python -m cookbooks.advisor recommend 2026-04
uv run python -m cookbooks.advisor review                # list open ConceptReviews
uv run python -m cookbooks.advisor accept rec_2026_04_<hash>
```

### 7. Ask questions

**Four ways to ask, depending on your context:**

#### a) From the CLI
```bash
uv run python -m cookbooks.knowledge_engine ask \
  "how much did I spend at Costco last year, broken down by month?"

# Use the DeepAgents 0.6 + researcher/synthesizer/critic loop:
PFH_QA_AGENT=deepagent \
  uv run python -m cookbooks.knowledge_engine ask \
  "what's my biggest discretionary spending category?"
```

#### b) From the web dashboard
```bash
bash scripts/dev.sh
# → http://127.0.0.1:3000/qa for natural-language Q&A
# → http://127.0.0.1:3000/graph/merchant::costco?depth=2 for subgraph
#   explorer (click 🔍 links on /qa to drill into nodes from tool calls)
```

See [Web frontend](#web-frontend) for the full route list.

#### c) From Claude Code (any session, any project)

The MCP server (`cookbooks/api/mcp_server.py`) exposes 5 read-only
tools — `cypher_read_only`, `sql_read_only`, `merchant_resolve`,
`evidence_for`, `neighbors` — over stdio. Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "openclaw": {
      "command": "uv",
      "args": ["run", "python", "-m", "cookbooks.api.mcp_server"],
      "cwd": "/path/to/personal_finance_helper",
      "env": {
        "PFH_LEDGER_BACKEND": "postgres",
        "PFH_PG_URL": "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw",
        "PFH_NEO4J_URL": "bolt://127.0.0.1:7687",
        "PFH_NEO4J_PASSWORD": "local-dev"
      }
    }
  }
}
```

Now any Claude Code session can ask "what did I spend at Costco last
March?" and it'll compose those 5 tools. Full runbook at
[`docs/runbook-mcp.md`](docs/runbook-mcp.md).

#### d) Read a specific wiki page directly
```bash
uv run python -m cookbooks.knowledge_engine read memo_2026_04
uv run python -m cookbooks.knowledge_engine read merchant_amazon
```

### 8. Daily/weekly operations

```bash
# Drop in new PDFs and re-ingest
uv run python -m cookbooks.statement_ingester backfill sources/

# Re-compile the graph (fingerprint-skip exits in <1s if nothing changed)
uv run python -m cookbooks._shared.compile_neo4j

# Set or update budgets / goals / net-worth snapshots
uv run python -m cookbooks.statement_ingester budget set 2026_04 category groceries 200
uv run python -m cookbooks.statement_ingester goal add "Emergency fund" 8000 2026-09 savings_account a_savings
uv run python -m cookbooks.statement_ingester networth snapshot 2026_04

# Consolidate duplicate merchants (e.g. "AMZN" + "Amazon UK")
uv run python -m cookbooks.statement_ingester dedupe-merchants --dry-run
uv run python -m cookbooks.statement_ingester dedupe-merchants --llm

# Repair categorisations after manual edits
uv run python -m cookbooks.statement_ingester reapply-rules
uv run python -m cookbooks.statement_ingester categorise-orphans
```

## Web frontend

Local-only Next.js + FastAPI dashboard under `web/` + `cookbooks/api/`.
Both processes hard-bind to `127.0.0.1`; CSP locks the page to its own
origin and the local API.

```bash
# One-time
cd web && pnpm install

# Both servers in one shell (Ctrl-C kills both)
bash scripts/dev.sh

# Or split:
uv run python -m cookbooks.api        # http://127.0.0.1:8000
cd web && pnpm dev                    # http://127.0.0.1:3000

# Production build
bash scripts/build-web.sh && cd web && pnpm start
```

**Routes:** `/` (overview), `/memos`, `/memos/[period]`, `/merchants`,
`/merchants/[id]`, `/recommendations`, `/recommendations/[id]`,
`/budgets`, `/goals`, `/networth`, `/forecast`, `/qa`, `/graph` (→
redirects to `/graph/merchant::costco?depth=2`), `/graph/[id]` (node
explorer — neighbors subgraph + adjacent transactions),
`/decisions/[id]`.

## Configuration

All env vars documented in [`.env.example`](.env.example). The most
important ones:

| Var | Default | Effect |
|---|---|---|
| `PFH_LLM_MODEL` | `ollama:qwen3.5:latest` | Provider + model passed to `langchain.chat_models.init_chat_model` |
| `PFH_ALLOW_REMOTE_LLM` | `false` | Set `true` to allow `openai:*` model ids — calls go through `_RedactingChat` |
| `PFH_PII_DENYLIST` | empty | Comma-separated names/strings tokenised before any remote call |
| `PFH_LEDGER_BACKEND` | `duckdb` | `duckdb` (in-process file) or `postgres` (Docker) |
| `PFH_PG_URL` | `postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw` | Postgres connection (raw form for `psycopg`; alembic needs `postgresql+psycopg://`) |
| `PFH_NEO4J_URL` | `bolt://127.0.0.1:7687` | Neo4j connection |
| `PFH_NEO4J_PASSWORD` | `local-dev` | — |
| `PFH_QA_AGENT` | `legacy` | `legacy` (hand-rolled loop) or `deepagent` (DeepAgents 0.6 + researcher/synthesizer/critic + 6 tools) |
| `PFH_CYPHER_DEFAULT_LIMIT` | `1000` | Implicit LIMIT for `cypher_read_only` |
| `PFH_CYPHER_TIMEOUT_S` | `5` | Cypher tx timeout (seconds) |
| `PFH_SQL_TIMEOUT_MS` | `5000` | Postgres `statement_timeout` |
| `PFH_CATEGORISE_CONCURRENCY` | `8` | Parallel LLM calls per file in the categoriser |

## Privacy contract

1. **Local by default.** The chat-model factory rejects every provider
   except `ollama` unless `PFH_ALLOW_REMOTE_LLM=true`.
2. **Round-trip tokenisation when remote.** `_RedactingChat` replaces
   detected PII with stable `<<CATEGORY_NNN>>` tokens on the way out
   and restores them on the way in. Two-layer detection: Presidio +
   spaCy NER for person names / locations, plus a regex pipeline for
   sort codes, IBANs, postcodes, phones, emails, NI numbers, card PANs
   (Luhn-validated), 8+ digit runs (upgraded to ACCT near "account"
   keywords).
3. **Fail-closed tripwire.** After tokenisation, `assert_no_pii()`
   re-scans with a tight regex set; any survivor raises `PIILeakError`
   before the HTTP call.
4. **bind_tools-safe.** `_RedactingChat.bind_tools(...)` re-wraps the
   bound model so the agent layer can't bypass redaction.
5. **Audit log records hashes only.** `data/openai_audit.jsonl`
   contains every redacted prompt plus the SHA-256 of the original —
   forensic proof of redaction without storing the leak.
6. **No source data ever committed.** `sources/`, `parsed/`, `wiki/`,
   `data/`, `graph/`, `.env`, `docker/.env`, `.claude/` are all
   gitignored.

Smoke-test with `bash scripts/check-egress.sh`.

## Repository layout

```
config/                       # settings.yaml
cookbooks/
  _shared/                    # primitives reused across cookbooks
    agents/                   # DeepAgents 0.6 wiring (qa_agent, subagents, profiles)
    analytics/                # spending, anomalies, budgets, memo_lint
    models/                   # auto-generated Pydantic models from the ontology
    ontology/                 # object_types / link_types / action_types YAML + loader + generators
    skills/                   # agent-facing skill files (PII rules, Cypher style, citation format, ...)
    tools/                    # cypher_read_only, sql_read_only, merchant_resolve (hybrid),
                              # evidence_for, neighbors, postgres_total_reconcile, safety guards
    compile_neo4j.py          # ledger + wiki → Neo4j (with vector-indexed Merchant.embedding)
    config.py                 # typed Settings + env loader
    db.py                     # ledger dispatcher (PFH_LEDGER_BACKEND)
    db_duckdb.py / db_postgres.py  # backend implementations
    embeddings.py             # sentence-transformers MiniLM-L6-v2 (local, 384-d)
    init_neo4j.py             # applies generated db/neo4j/init.cypher
    llm.py                    # init_chat_model factory + _RedactingChat
    neo4j_client.py           # singleton driver wrapper
    pii.py + pii_ner.py + pii_tokenizer.py  # redaction stack
    qa_tools.py               # read_wiki_page, merge_merchants
    record_ingester.py        # manifest-driven CSV ingest
  statement_ingester/         # parse → upsert → categorise → recurring → report
  monthly_analyst/            # rollups → budget variance → forecast → anomalies → goals → memo
  knowledge_engine/           # legacy Q&A CLI (dispatches to deepagent path on PFH_QA_AGENT=deepagent)
  advisor/                    # recommendations + concept-review queue
  api/                        # FastAPI server + routers + MCP server
db/                           # Neo4j init.cypher (generated) + Postgres alembic migrations
docker/                       # docker-compose.yml (Postgres + Neo4j)
docs/
  architecture.md             # current architecture
  runbook-postgres.md         # Postgres setup
  runbook-rebuild-graph.md    # full clean rebuild
  runbook-mcp.md              # MCP server in Claude Code
  runbook-graph-viz.md        # /graph/[id] explorer
  runbook-pii-models.md       # spaCy + Presidio install
  superpowers/specs/          # design specs (the openclaw upgrade spec lives here)
  superpowers/plans/          # plans 1-4 + tier-3 vector merchant resolve
eval/                         # YAML-driven regression suites + reporter
scripts/
  setup.sh                    # one-shot first-time install
  dev.sh                      # run API + web together
  build-web.sh                # production build
  check-egress.sh             # confirm no outbound network with PFH_ALLOW_REMOTE_LLM unset
  migrate_wiki_to_postgres.py # one-time wiki → Postgres migration
web/                          # Next.js dashboard
tests/                        # 584 unit + eval tests (all synthetic fixtures)
```

## Tests

```bash
uv run pytest --tb=no -p no:warnings
```

The integration test `tests/statement_ingester/test_real_backfill.py`
is skipped by default; it needs `PFH_RUN_INTEGRATION=1` and a running
Ollama. All other tests use synthetic data (no real PII).

Several tests use **testcontainers-python** to spin up ephemeral
Postgres and Neo4j containers — they skip cleanly when Docker isn't
running.

### Regression evals

YAML-driven suites under `cookbooks/<name>/evals/` exercise each
cookbook end-to-end with synthetic fixtures and deterministic
assertions:

```bash
uv run pytest tests/eval -m eval
```

A markdown summary lands at `eval/out/report.md` (gitignored). Add a
new case by editing the suite YAML.

## License

Private — not yet published. See repo owner.
