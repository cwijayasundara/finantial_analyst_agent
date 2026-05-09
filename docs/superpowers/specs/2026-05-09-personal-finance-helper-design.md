# Personal Finance Helper — Design Spec

**Date:** 2026-05-09
**Working title:** `personal-finance-helper` (codename *openclaw*)
**Author:** Chaminda Wijayasundara
**Status:** Draft for implementation planning

## 1. Goal

A privacy-first, locally-hosted personal financial analyser, advisor, and budget manager. Ingests PDF bank and credit-card statements, normalises them into a typed datastore, and exposes a multi-cookbook agentic surface for natural-language analysis, monthly memos, and actionable recommendations. Designed to grow open-source ("openclaw"): each new workflow is a self-contained cookbook directory, contributable as a single PR.

**Non-goals (v1):** investment portfolio tracking, tax-return generation, mortgage scenario analysis, FX normalisation, fraud detection. These are scoped as v2 cookbooks following the same shape.

**Privacy thesis:** no source data, parsed data, derived data, prompts, or completions leave the machine.
- FastAPI server binds `127.0.0.1` only.
- Ollama base URL pinned to `http://127.0.0.1:11434`.
- No telemetry, no analytics, no error reporting.
- Network egress is enforced by *not configuring any remote endpoint*; nothing in the system requires a network call after install. The repo ships a `scripts/check-egress.sh` smoke test that asserts no outbound socket is opened during a representative cookbook run.

## 2. Inputs and Scope

- 17 months of statements (Jan 2025 – May 2026) under `sources/`:
  - `sources/savings_stmt/*.pdf` — bank statements
  - `sources/crdit_stmt/*.pdf` — credit card statements
- Hardware target: Apple Silicon Mac, 32GB+ RAM
- Local LLM via Ollama: `gemma4:e4b` primary, `qwen3:14b` available as per-subagent escape hatch
- Embeddings: `nomic-embed-text` (Ollama)

## 3. Architectural Principles

| # | Principle | Consequence |
|---|---|---|
| 1 | **Layered datastore** | `sources/` immutable → `parsed/` cached → `ledger.duckdb` raw rows → `wiki/` curated pages → `graph/kuzu.db` derived |
| 2 | **SQL-first analytics** | DuckDB is the workhorse for aggregations; Kuzu (Cypher) handles multi-hop, provenance, decision lineage |
| 3 | **Two-runtime split** | LangGraph `StateGraph` for ingestion (deterministic ETL); DeepAgents for reasoning (open-ended). They share the data substrate, not the runtime |
| 4 | **Three-tier subagent isolation** | Reader (touches untrusted PDFs/parsed) → Critic (no untrusted, no Write) → Writer (only one with Write, governed via Action Types) |
| 5 | **Cookbook-per-workflow** | Each user-facing workflow is a self-contained directory: `agent.yaml` + `subagents/` + `skills/` + `steering-examples.json` + `evals/` |
| 6 | **Schema'd I/O between agents** | Every subagent uses Pydantic `response_format`; cross-cookbook handoffs go through schema-validated `handoff_request` |
| 7 | **Wiki canonical for interpretation; ledger canonical for raw data; graph derived from both** | If `wiki` and `graph` disagree, recompile graph from wiki+ledger |
| 8 | **Every Action invocation audited** | `audit.jsonl` row + typed `Decision` wiki page + replayable trace (lifted from `context_graphs`) |
| 9 | **Provenance is first-class** | Every claim cites a `Statement` page or transaction id; completeness checker prevents silent extraction loss |
| 10 | **Provider-neutral** | `init_chat_model("ollama:...")` ↔ `"anthropic:..."` ↔ `"openai:..."` swappable per cookbook in one line |

## 4. Data Architecture

### Layer model

```
L0   sources/*.pdf                     immutable; agents read only
L0.5 parsed/<sha256>.{md,json}         Docling cache; never hand-edit
L1a  ledger.duckdb                     normalised transactions; record-ingester writes
L1b  wiki/                             LLM-curated, governed writes
       merchants/<slug>.md             canonical merchant + aliases + category
       subscriptions/<slug>.md         detected recurring patterns
       statements/<id>.md              one per source PDF
       memos/<period>.md               monthly analyst output
       decisions/<id>.md               advisor recommendations (audited)
       annotations/<txn-id>.md         user manual notes
L2   graph/kuzu.db                     derived; compile_graph.py rebuilds idempotently
     graph/snapshots/graph.jsonl       fallback when kuzu absent
     graph/audit.jsonl                 one row per Action invocation
L3   ontology/                         Object/Link/Action types — the contract
L4   cookbooks/, api/, web/            agents and surfaces
```

### DuckDB schema (L1a)

```sql
accounts(id, name, type, currency, holder)
statements(id, account_id, period_start, period_end, source_pdf, sha256, parser_used)
transactions(
    id, date, amount, raw_description, account_id, statement_id,
    merchant_id,           -- FK into wiki/merchants by slug
    category_id,           -- FK into categories.id
    pattern_id             -- FK into patterns.id (NULL when one-off)
)
merchants(id, canonical_name, category_id, aliases JSON)   -- mirrored from wiki/merchants/
categories(id, name, parent_id)                            -- hierarchical tree
patterns(id, merchant_id, cadence, expected_amount, last_seen, confidence)
annotations(transaction_id, note, kind)                    -- mirrored from wiki/annotations/
memos(id, period, body_md, citations JSON)                 -- pointer to wiki/memos/
```

`merchants`, `annotations`, and `memos` are mirrored from the wiki; the wiki is canonical, DuckDB is a queryable projection. `compile_graph.py` is the authoritative reconciler.

### Ontology (L3)

**Object Types:** `Account`, `Statement`, `Transaction`, `Merchant`, `Category`, `Subscription`, `Memo`, `Decision`, `Annotation`.

**Link Types:**
- `from_account` — Transaction → Account
- `in_statement` — Transaction → Statement
- `at_merchant` — Transaction → Merchant
- `aliases` — Merchant → Merchant (surface-form merging)
- `categorised_as` — Merchant → Category
- `parent_of` — Category → Category
- `recurring_at` — Subscription → Merchant
- `deviates_from` — Transaction → Subscription (price drift / missed charge)
- `funded_by` — Transaction → Transaction (cc payment funded by savings transfer)
- `cites` — Memo|Decision → Statement|Merchant|Subscription|Transaction
- `triggered_by` — Decision → Memo
- `affects` — Decision → Merchant|Subscription|Category
- `flags` — Annotation → Transaction

**Action Types** (governed writes; every invocation audited):
- `publish_monthly_memo`
- `publish_recommendation`
- `confirm_recurring_pattern`
- `merge_merchant_aliases`
- `flag_concept_review`

## 5. Ingestion Runtime — LangGraph StateGraph

A deterministic ETL pipeline with one optional LLM node (categoriser). Lives at `cookbooks/statement-ingester/`. Triggered by CLI, FastAPI `/upload`, or a `watchdog` filesystem observer.

### Nodes and edges

```
parse_pdf ─[failure]─▶ fallback_parser ─┐
    │                                   │
    └─[success]─────────────────────────┴─▶ validate_completeness
                                                │
                                                ▼
                                         upsert_ledger
                                                │
                              [new merchants?]──┴─[no]──▶ detect_recurring
                                       │                        │
                                       ▼                        │
                                  categorise (LLM)              │
                                       │                        │
                                       └────────────────────────┘
                                                │
                                                ▼
                                         compile_graph
                                                │
                                                ▼
                                            report → END
```

### Node responsibilities

| Node | Logic | Idempotency |
|---|---|---|
| `parse_pdf` | Docling primary | `parsed/<sha256>.md` cached |
| `fallback_parser` | LiteParse → MarkItDown chain when Docling fails | same cache |
| `validate_completeness` | Regex-scan parsed.md for currency values; assert each appears in extracted tables; emit warnings (do not block) | pure |
| `upsert_ledger` | record-ingester maps tables → DuckDB rows; upserts merchants/statements pages | `INSERT OR IGNORE` keyed on `(account_id, date, amount, raw_description)`; sha256 of source short-circuits whole run |
| `categorise` | LLM (`gemma4:e4b` + Pydantic `CategorisationResult`) on unknown merchants only; cache to `data/rules.yaml` | rules.yaml lookup first |
| `detect_recurring` | DuckDB window functions surface candidates; LLM confirms via `upsert_subscription` Action | content-hash on candidate set |
| `compile_graph` | reads ledger + wiki, projects to Kuzu; falls back to JSONL snapshot when kuzu unavailable | mtime+size fingerprint over wiki + ontology + ledger schema |
| `report` | emit `IngestReport(processed, skipped, warnings, new_merchants, errors)` | pure |

### State

```python
class IngestState(TypedDict):
    source_path: str
    sha256: str
    parser_used: Literal["docling", "liteparse", "markitdown"] | None
    parsed_md_path: str | None
    parsed_tables: list[dict]
    completeness_warnings: list[str]
    new_transactions: list[Transaction]
    new_merchants: list[str]
    categorised: list[CategorisationResult]
    recurring_detected: list[Subscription]
    graph_compiled: bool
    errors: list[str]
    skipped_reason: str | None
```

### Triggers

```bash
python -m cookbooks.statement_ingester run sources/savings_stmt/2026_May_Statement.pdf
python -m cookbooks.statement_ingester backfill sources/      # all PDFs, idempotent
python -m cookbooks.statement_ingester watch sources/         # watchdog mode
# or via API: POST /api/cookbooks/statement-ingester  with { "trigger": "Ingest <path>" }
```

## 6. Reasoning Runtime — DeepAgents Cookbooks

Every reasoning workflow is a self-contained cookbook directory under `cookbooks/`, mirroring Anthropic's managed-agent shape but adapted for DeepAgents and local Ollama.

### Cookbook anatomy

```
cookbooks/<name>/
├── README.md               # purpose, security tier, handoffs, steering triggers
├── agent.yaml              # orchestrator config (DeepAgents manifest)
├── steering-examples.json  # canonical invocation prompts (also seed evals)
├── subagents/
│   ├── <reader>.yaml       # SQL/Cypher tools only; no Write
│   ├── <critic>.yaml       # validates reader output; no Write
│   └── <writer>.yaml       # ONLY leaf with Write — produces /out or wiki/ via Action
├── skills/                 # markdown skill files loaded by SkillsMiddleware
├── evals/                  # YAML eval suites
└── tests/
```

### Shared infrastructure

```
cookbooks/_shared/
├── ontology/               # Object/Link/Action types (one source of truth)
├── tools/                  # execute_sql, execute_cypher, retrieve_context, save_memory
├── middleware/             # ModelCallLimit, ToolCallLimit, ModelRetry, ToolRetry
├── skills/                 # cross-cookbook skills (citation-rules, completeness-discipline)
├── handoff.py              # handoff_request router (allowlisted, schema-validated, audited)
├── llm.py                  # init_chat_model wrapper, per-subagent override
└── loader.py               # reads agent.yaml → builds create_deep_agent(...)
```

### Manifest shape (DeepAgents-flavoured, deliberately close to Anthropic's)

```yaml
# cookbooks/expense-analyser/agent.yaml
name: expense-analyser
model: ollama:gemma4:e4b
fallback_model: ollama:qwen3:14b   # per-subagent override allowed

system:
  file: ./skills/orchestrator.md

middleware:
  - { type: model_call_limit, run_limit: 50 }
  - { type: tool_call_limit,  run_limit: 200 }
  - { type: model_retry,      max_retries: 3, backoff: 2.0 }
  - { type: tool_retry,       max_retries: 3, backoff: 2.0 }

filesystem:
  routes:
    /sources: { backend: read_only, root: ../../sources }
    /parsed:  { backend: read_only, root: ../../parsed }
    /wiki:    { backend: read_only, root: ../../wiki }
    /out:     { backend: filesystem, root: ./out }

tools:
  shared:
    - execute_sql
    - execute_cypher
    - retrieve_context
    - save_memory

callable_agents:
  - { manifest: ./subagents/reader.yaml,      permissions: deny_write }
  - { manifest: ./subagents/critic.yaml,      permissions: deny_write }
  - { manifest: ./subagents/memo-writer.yaml, permissions: write_via_action_only }

steering_examples:
  file: ./steering-examples.json
```

The loader builds the DeepAgents call:

```python
agent = create_deep_agent(
    model=init_chat_model(cfg.model),
    tools=resolve_shared_tools(cfg.tools.shared),
    subagents=[load_subagent(s) for s in cfg.callable_agents],
    middleware=build_middleware_stack(cfg.middleware),
    backend=CompositeBackend(
        default=StateBackend(),
        routes=resolve_filesystem_routes(cfg.filesystem.routes),
    ),
    skills_paths=[Path(s) for s in resolve_skill_paths(cfg)],
)
```

### Three-tier subagent permissions (FilesystemPermission-enforced)

| Role | Read | Write | Tools |
|---|---|---|---|
| Reader | `/sources`, `/parsed`, `/wiki`, `/out` | none (`[deny, /]`) | `execute_sql`, `execute_cypher`, `retrieve_context` |
| Critic | `/wiki`, `/out` | none | `execute_sql`, `execute_cypher`, `retrieve_context` |
| Writer | `/wiki`, `/out` | `/out` (unrestricted within the cookbook's mounted `out` route); `/wiki` writes ONLY through Action-Type tools (`upsert_memo`, `upsert_decision`, `confirm_recurring_pattern`, `merge_merchant_aliases`, `flag_concept_review`) — direct filesystem writes to `/wiki` are denied by `FilesystemPermission` even on the writer | governed `upsert_*` via action server |
| Orchestrator | all read | none — must delegate | `task` only |

### Typed subagent outputs

Every subagent declares a Pydantic `response_format`. Examples:

```python
class CategorisationResult(BaseModel):
    merchant_canonical: str
    category: Literal["groceries", "fuel", "dining", "subscription",
                      "income", "transfer", "utilities", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_short: str = Field(max_length=200, regex=r"^[\w\s,.\-£$]+$")

class MonthlyMemo(BaseModel):
    period: str = Field(regex=r"^\d{4}-\d{2}$")
    body_md: str
    citations: list[str] = Field(min_length=3)
    flagged_anomalies: list[str]

class Recommendation(BaseModel):
    title: str = Field(max_length=120)
    rationale_md: str
    expected_monthly_saving_gbp: float
    affected_merchants: list[str]
    citations: list[str] = Field(min_length=1)
```

Character-class restrictions on free-text fields defang prompt injection from untrusted PDFs (matches `context_graphs` and `deepagents_impl` patterns).

### Cross-cookbook handoffs

Lifted from Anthropic's `orchestrate.py`. A cookbook never calls another cookbook directly. It emits:

```json
{
  "handoff_request": {
    "target": "budget-advisor",
    "trigger": "Recommend savings on category 'subscriptions'",
    "payload": {
      "context_memo_id": "memo:2026-01",
      "anomalies_observed": ["streaming_drift", "groceries_spike"]
    }
  }
}
```

`cookbooks/_shared/handoff.py`:
- Hard-allowlists target cookbooks per source cookbook
- Schema-validates payload against the target's input schema
- Records each handoff in `audit.jsonl`
- Routes as a fresh DeepAgents invocation in a new session

### Cookbook roster (v1 + v2)

| Cookbook | Phase | Reader | Middle | Writer | Steering trigger |
|---|---|---|---|---|---|
| `statement-ingester` | v1 (LangGraph) | parse | validate, upsert, categorise, recurring | compile_graph | `Ingest <pdf>` |
| `data-agent` | v1 | sql-runner | self-corrector | (no writer) | `Q: <natural language>` |
| `expense-analyser` | v1 | reader | critic | memo-writer | `Memo for <YYYY-MM>` |
| `visualiser` | v1 | spec-builder | — | chart-writer | `Chart <metric> for <period>` |
| `budget-advisor` | v1 | pattern-reader | critic | decision-writer | `Recommend savings on <category|merchant>` |
| `subscription-auditor` | v1 | recurring-reader | drift-detector | decision-writer | `Audit subscriptions` |
| `balance-tracker` | v1 | balance-reader | projector | report-writer | `Net worth and 6mo projection` |
| `tax-prep` | v2 | hmrc-reader | classifier | return-writer | `Self-assessment for <tax-year>` |
| `retirement-projection` | v2 | pension-reader | projector | plan-writer | `Retire-by-<age> plan` |
| `mortgage-analyser` | v2 | mortgage-reader | scenario-runner | plan-writer | `Overpay scenarios` |
| `fx-multi-currency` | v2 | fx-reader | normaliser | rate-writer | `Normalise to <CCY>` |
| `fraud-detector` | v2 | anomaly-reader | scorer | alert-writer | `Scan for anomalies <period>` |
| `bill-predictor` | v2 | history-reader | forecaster | forecast-writer | `Predict bills <next-month>` |
| `debt-paydown` | v2 | debt-reader | optimiser | plan-writer | `Avalanche vs snowball` |
| `goal-tracker` | v2 | goal-reader | progress-checker | status-writer | `Goal progress` |

## 7. Data Agent (port of `openai_data_agent_clone`)

The `cookbooks/data-agent/` cookbook ports the existing `openai_data_agent_clone` from OpenAI Agents SDK + GPT-4o to DeepAgents + Ollama, preserving the architecture the user already validated.

### What ports unchanged

- **Six-layer context system**: Schema, Annotations, Code Enrichment, Knowledge Base, Memory (global + personal), Runtime
- **Tool surface**: `execute_sql`, `search_schema`, `retrieve_context`, `save_memory` — same names, same signatures
- **ReAct self-correction loop**: empty result → investigate → save learning → retry
- **Memory system**: JSON files (global + personal); persisted across sessions
- **Eval framework**: YAML evalsets with SQL semantic comparison + DataFrame matching + LLM grading
- **DuckDB warehouse**: kept as-is (analytical queries are its sweet spot; embedded; fits "no data leaves machine")

### What changes

| Concern | Before | After |
|---|---|---|
| Agent framework | OpenAI Agents SDK | `deepagents.create_deep_agent` |
| LLM | `gpt-4o` | `init_chat_model("ollama:gemma4:e4b")` |
| Embeddings | `text-embedding-3-small` | `ollama:nomic-embed-text` |
| Specialist agents | SDK handoffs (`SQLExpert`, `Visualizer`) | DeepAgents `SubAgent` dicts (`sql-expert.yaml`, `visualiser.yaml`) |
| Tool addition | — | `execute_cypher` (Kuzu) added alongside `execute_sql` |
| Resilience | implicit | explicit `ModelRetryMiddleware`, `ToolRetryMiddleware`, `PatchToolCallsMiddleware` |
| Cost cap | implicit | explicit `ModelCallLimitMiddleware(50)`, `ToolCallLimitMiddleware(200)` |

### Hybrid SQL/Cypher routing

System prompt rule: *aggregations, sums, GROUP BY, time-series → SQL; provenance, multi-hop relationships, decision lineage, fund flows, recurring-pattern reasoning → Cypher; when unsure, try SQL first.*

A small library of Cypher recipes ships in the prompt as few-shot to compensate for `gemma4:e4b` knowing SQL much better than Cypher (same trick `context_graphs` uses).

## 8. Web and CLI Surfaces

### FastAPI

Mounts every cookbook automatically. Binds `127.0.0.1` only.

```
POST /api/cookbooks/<name>           { trigger: str, payload?: dict }
GET  /api/cookbooks                  list of cookbooks + status
POST /api/upload                     multipart PDF → triggers statement-ingester
GET  /api/dashboard/monthly          aggregated charts data
GET  /api/memos[/period]             memo listing / individual
GET  /api/decisions                  recommendation listing
POST /api/sql                        raw SQL execution (read-only)
POST /api/query                      natural language → data-agent
GET  /api/schema                     ledger schema introspection
GET  /api/categories                 category tree (editable)
PATCH /api/categories/{id}           edit category — invalidates rules cache
```

### React + Vite frontend

| Page | Purpose |
|---|---|
| Dashboard | monthly income vs expenses, top merchants, category trends, balance over time (Plotly via `react-plotly.js`) |
| Statements | list of ingested PDFs; drill-down to individual transactions |
| Categories | editable category tree; corrections feed `rules.yaml` |
| Chat | natural-language interface to `data-agent` cookbook with citation rendering |
| Memos | rendered `wiki/memos/*.md` with cited statement links |
| Recommendations | rendered `wiki/decisions/*.md` with audit trail |
| Cookbooks | discovery surface; trigger any cookbook with the steering example as scaffold |

### CLI

```bash
python -m cookbooks.<name> "<steering trigger>"
python -m cookbooks.statement_ingester backfill sources/
python -m cookbooks.expense_analyser "Memo for 2026-01"
python -m cookbooks.budget_advisor "Recommend savings on subscriptions"
python -m eval.runner cookbooks/expense-analyser/evals/monthly_memo.yaml
```

## 9. Local Inference (Ollama)

Single-tier default. Per-subagent escape hatch.

| Role | Model | Size (Q4_K_M) | Notes |
|---|---|---|---|
| Default (everywhere) | `ollama:gemma4:e4b` | already installed | MoE 4B-effective; fast on M-series 32GB |
| Embeddings | `ollama:nomic-embed-text` | ~270MB | RAG over wiki + KB |
| Escape hatch (analyst, advisor) | `ollama:qwen3:14b` | ~9GB | swap if memo quality lags |

Mitigations for `gemma4:e4b` weak spots, all already in the design:
- `PatchToolCallsMiddleware` (auto) — repairs malformed tool calls
- `ToolRetryMiddleware(3)` — retries transient failures
- Pydantic `response_format` — rejects malformed structured output
- Cypher recipe library in prompts — text-to-Cypher quality boost
- ReAct self-correction loop — empty results trigger investigation, not silent failure

## 10. Evaluation

Per-cookbook `evals/` directory with YAML test suites. Each suite ports the existing `openai_data_agent_clone` evaluator: SQL semantic comparison, DataFrame matching, LLM-as-judge grading (judge runs on `qwen3:14b` to avoid grading bias from the same model under test).

Steering examples double as eval seeds — adding a steering example automatically adds a regression test.

A cross-cookbook eval runner at `eval/runner.py` runs all suites, emits a JSON report, and gates CI.

## 11. Implementation Phases

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **P1** | `_shared/` (loader, tools, middleware, ontology) + `statement-ingester` cookbook (LangGraph). | All 17 PDFs ingested into DuckDB + wiki + Kuzu. Completeness checker passes. `python -m cookbooks.statement_ingester backfill sources/` is idempotent (second run = no-op in seconds). |
| **P2** | `data-agent` cookbook (NL → SQL/Cypher with 6-layer context, ported from `openai_data_agent_clone`). | "How much at Tesco in Jan vs Feb 2026?" returns cited answer with SQL shown. Self-correction triggers on a synthetic broken query. |
| **P3** | `expense-analyser` + `visualiser` cookbooks. | One memo per month exists with ≥3 citations and ≥1 chart. |
| **P4** | FastAPI cookbook-mount layer + React UI. | Dashboard renders 17-month charts; chat answers cited NL queries; statement uploader triggers ingestion. |
| **P5** | `budget-advisor` + `subscription-auditor` + `balance-tracker` cookbooks. Decision pages. Handoff router. | Get 3 ranked recommendations with expected monthly savings; every recommendation has an audit row + Decision page; net-worth + 6mo cash-flow projection rendered in the dashboard. |
| **P6** | Per-cookbook eval CI. Documentation site. v1.0 tag. | ≥80% pass on a 20-question eval suite per cookbook. |
| **V2** | Community cookbooks: tax-prep, retirement, mortgage, fx, fraud, bill-predictor, debt-paydown, goal-tracker. Each PR-sized. | Each new cookbook = one directory, no Python plumbing required. |

## 12. Repository Layout

```
personal_finance_helper/
├── cookbooks/
│   ├── README.md                       # cookbook table (top-level discovery)
│   ├── _shared/
│   │   ├── ontology/{object_types,link_types,action_types}.yaml
│   │   ├── ontology/functions/actions.py    # governed write impls + audit logger
│   │   ├── tools/{sql,cypher,context,memory,visualise,governed}.py
│   │   ├── middleware/__init__.py           # deepagents production stack
│   │   ├── skills/{citation-rules,completeness-discipline}.md
│   │   ├── handoff.py
│   │   ├── llm.py
│   │   ├── loader.py                        # agent.yaml → create_deep_agent
│   │   └── compile_graph.py                 # ledger + wiki → kuzu
│   ├── statement-ingester/                  # LangGraph ETL
│   │   ├── README.md
│   │   ├── flow.yaml
│   │   ├── steering-examples.json
│   │   ├── nodes/{parse,validate,upsert,categorise,recurring,compile,report}.py
│   │   ├── state.py
│   │   ├── graph.py
│   │   ├── cli.py
│   │   ├── skills/
│   │   ├── evals/
│   │   └── tests/
│   ├── data-agent/                          # ported from openai_data_agent_clone
│   ├── expense-analyser/
│   ├── visualiser/
│   ├── budget-advisor/
│   ├── subscription-auditor/
│   └── balance-tracker/
├── api/
│   ├── main.py                              # FastAPI app, 127.0.0.1
│   ├── routes/{query,upload,dashboard,memos,decisions,categories,cookbooks}.py
│   └── schemas.py
├── web/                                     # React + Vite + TypeScript
│   ├── src/components/{Dashboard,Statements,Categories,Chat,Memos,Recommendations,Cookbooks}.tsx
│   └── package.json
├── eval/runner.py
├── data/
│   ├── ledger.duckdb
│   ├── vectors.chroma/                      # or sqlite_fts/ — see §13
│   ├── annotations/                         # 6-layer context: YAML per table/column
│   ├── knowledge/                           # KB docs (parser quirks, statement formats)
│   ├── memory/{global,personal}.json
│   └── rules.yaml                           # cached merchant→category mappings
├── sources/                                 # YOUR PDFs (immutable)
│   ├── crdit_stmt/
│   └── savings_stmt/
├── parsed/                                  # Docling cache (gitignored)
├── wiki/
│   ├── merchants/  subscriptions/  statements/  memos/  decisions/  annotations/
├── graph/
│   ├── kuzu.db
│   ├── snapshots/graph.jsonl
│   └── audit.jsonl
├── ontology/                                # symlink to cookbooks/_shared/ontology/
├── docs/
│   └── superpowers/specs/2026-05-09-personal-finance-helper-design.md
├── config/settings.yaml
├── pyproject.toml
└── README.md
```

## 13. Open Decisions

- **RAG store: ChromaDB vs SQLite FTS5.** Default: SQLite FTS5 (no service, single file, sufficient for short merchant/memo text). Reconsider if semantic recall fails on real questions.
- **Watch mode in v1?** `watchdog`-based filesystem observer is cheap; ship in P1 if time permits, else punt to V2.
- **MCP server wrapper.** Expose cookbook tools as a local MCP server later for IDE/Claude Desktop integration. Not v1.
- **Multi-currency handling.** Punt to v2 (`fx-multi-currency` cookbook). v1 assumes single currency per account.
- **Optional Neo4j-in-Docker sidecar.** Kuzu is the runtime graph store (embedded, fits the no-daemon ethos, validated in `context_graphs`). For occasional visual exploration of the graph (debugging ontology widening, tracing fund-flow chains), ship a `cookbooks/_shared/export_neo4j.py` that spins up a Neo4j container, imports `kuzu.db` via Cypher script, and opens `localhost:7474`. Sidecar only — never a runtime dependency.

## 14. References

- Anthropic managed-agent cookbooks: <https://github.com/anthropics/financial-services/tree/main/managed-agent-cookbooks>
- DeepAgents (LangChain v1+): <https://docs.langchain.com/oss/python/deepagents/overview>
- Existing implementations the user validated:
  - `claude_financial_services/deepagents_impl/` — DeepAgents middleware stack
  - `rnd_2026/context_graphs/` — wiki + ontology + Kuzu + audited Decisions + replay
  - `rnd_2026/openai_data_agent_clone/` — six-layer context, ReAct self-correction, memory
- Docling: <https://docling-project.github.io/docling/>
- Kuzu: <https://kuzudb.com/>
- TrustGraph "context graph" thesis: <https://trustgraph.ai/guides/key-concepts/context-graphs/>
