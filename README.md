# Personal Finance Helper (codename *openclaw*)

Privacy-first, locally-hosted personal finance analyser, advisor, and
budget manager. Ingests PDF bank and credit-card statements, normalises
into a typed graph + ledger, and exposes a multi-cookbook agentic
surface for monthly memos, Q&A, budget tracking, and actionable
recommendations.

**Status:** P1–P5 all shipped. **315 unit tests passing.** Architecture
is documented in [`docs/architecture.md`](docs/architecture.md).

## What it does

| Cookbook | Phase | What it produces |
|---|---|---|
| [`statement_ingester`](cookbooks/statement_ingester/) | P1 | DuckDB ledger + Wiki pages from your PDFs |
| [`monthly_analyst`](cookbooks/monthly_analyst/) | P2 | One Markdown memo per period with rollups + anomalies + budget variance |
| [`knowledge_engine`](cookbooks/knowledge_engine/) | P3 | Q&A agent over the graph + wiki, with `[[wikilink]]` citations |
| [`advisor`](cookbooks/advisor/) | P5 | Recommendations (cancel sub, adjust budget, investigate anomaly) + concept-review queue |
| Budget management | P4 | `Budget` ObjectType + `set_budget` action; surfaces in the analyst's memos |

Plans for each phase live under [`docs/superpowers/plans/`](docs/superpowers/plans/).

## Setup

```bash
# One-time
bash scripts/setup.sh
ollama pull qwen3.5:latest nomic-embed-text       # any small chat model is fine
ollama serve &

# Optional: opt into remote LLM (off by default)
cp .env.example .env
# Edit .env to set:
#   PFH_ALLOW_REMOTE_LLM=true
#   PFH_LLM_MODEL=openai:gpt-4o-mini  (or any provider supported by langchain.chat_models.init_chat_model)
#   OPENAI_API_KEY=sk-...
#   PFH_PII_DENYLIST=YOURSURNAME,FAMILYSURNAME
```

## Daily workflow

```bash
# 1. Drop new PDFs into sources/<account-folder>/, then ingest
python -m cookbooks.statement_ingester backfill sources/

# 2. (Optional) Set or update budgets
python -m cookbooks.statement_ingester budget set 2025_04 category groceries 200
python -m cookbooks.statement_ingester budget list 2025_04

# 3. Generate the monthly memo for a period
python -m cookbooks.monthly_analyst analyse 2025-04
# … or backfill an inclusive range:
python -m cookbooks.monthly_analyst backfill-memos 2025-01 2026-05

# 4. Get actionable recommendations
python -m cookbooks.advisor recommend 2025-04
python -m cookbooks.advisor review                # list open ConceptReviews
python -m cookbooks.advisor accept rec_2025_04_<hash>

# 5. Ask questions
python -m cookbooks.knowledge_engine ask \
  "what was my biggest spending category in April 2025?"
python -m cookbooks.knowledge_engine query \
  "MATCH (m:Entity) WHERE m.type='Merchant' RETURN m.id LIMIT 5"
python -m cookbooks.knowledge_engine read merchant_amazon
```

### Operations / curation

```bash
# Visualise the trust/context graph
python -m cookbooks.statement_ingester graph-stats
open graph/visualization/graph.html

# Consolidate duplicate merchants
python -m cookbooks.statement_ingester dedupe-merchants --dry-run
python -m cookbooks.statement_ingester dedupe-merchants --llm    # semantic merges (AMZN -> amazon)

# Repair categorisations after manual edits
python -m cookbooks.statement_ingester reapply-rules
python -m cookbooks.statement_ingester categorise-orphans

# Regenerate Obsidian wikilinks across the wiki
python -m cookbooks.statement_ingester rebuild-wiki

# Replay: what was live when this Decision was written?
python -m cookbooks.monthly_analyst replay decision_publish_monthly_memo_analyst_*
```

## Configuration

All env vars are documented in [`.env.example`](.env.example). Highlights:

| Var | Default | Effect |
|---|---|---|
| `PFH_LLM_MODEL` | `ollama:qwen3.5:latest` | Provider + model passed to `langchain.chat_models.init_chat_model` |
| `PFH_ALLOW_REMOTE_LLM` | `false` | Set `true` to allow `openai:*` model ids |
| `PFH_PII_DENYLIST` | empty | Comma-separated names/strings replaced with `[NAME]` before any remote call |
| `PFH_CATEGORISE_CONCURRENCY` | `8` | Parallel LLM calls per file in the categoriser |
| `PFH_BUDGET_TOLERANCE` | `0.05` | Variance % under which a budget is "on_track" |
| `PFH_SUB_DEV_TOL` | `0.05` | Subscription-drift threshold for anomalies |
| `PFH_OUTLIER_Z` | `2.0` | Merchant z-score threshold for monthly outliers |
| `PFH_MEMO_MODE` | `template` | Memo body mode; `llm` adds optional polish |
| `PFH_MEMO_LINT_WARN_ONLY` | `false` | When `true`, fabricated numbers in memos warn instead of raising |
| `PFH_QA_ROW_LIMIT` | `200` | Cypher row cap for the Q&A `query_graph` tool |

## Privacy contract

1. **Local by default.** The chat model factory rejects every provider
   except `ollama` unless `PFH_ALLOW_REMOTE_LLM=true` is set.
2. **PII masker before the wire.** When remote is enabled, every prompt
   passes through a regex masker (sort code, IBAN, postcode, phone,
   email, 8+ digit run) plus a configurable denylist.
3. **Post-mask hard guard.** `assert_no_pii()` runs after masking; if
   any high-risk pattern survived, the call is refused (raises
   `PIILeakError`) — no payload reaches the wire.
4. **Audit log.** Every remote LLM call is appended to
   `data/openai_audit.jsonl` (prompt + response). The repository's
   `.gitignore` excludes this file.
5. **No source data ever committed.** `sources/`, `parsed/`, `wiki/`,
   `data/`, `graph/`, `.env`, `.claude/` are all gitignored.

Run `bash scripts/check-egress.sh` to smoke-test that no remote calls
fire by default.

## Frontend

There is **no web UI** in this repository. All interaction is through
the CLIs above plus Obsidian (which can render `wiki/` directly). A
React or Next.js dashboard is **out of scope for v1**; if it lands
later, it would be a separate `web/` package consuming the existing
DuckDB ledger + Markdown wiki + Kuzu graph as read-only data sources
(or via a thin FastAPI layer over `cookbooks/_shared/qa_tools.py`).

## Repository layout

```
config/                       # settings.yaml
cookbooks/
  _shared/                    # primitives reused across cookbooks
    analytics/                # spending, anomalies, budgets, memo_lint
    ontology/                 # object_types, link_types, action_types YAML + loader + actions.py
    config.py                 # typed Settings + loopback validator
    db.py                     # DuckDB schema + connections
    llm.py                    # init_chat_model factory + AuditingChat
    pii.py                    # mask_pii + assert_no_pii + denylist
    qa_tools.py               # query_graph / read_wiki_page / merge_merchants
    query.py                  # read-only Cypher executor
    record_ingester.py        # manifest-driven CSV ingest
    compile_graph.py          # ledger + wiki -> Kuzu + JSONL
  statement_ingester/         # P1 cookbook (parse -> validate -> upsert -> categorise -> recurring -> compile -> report)
  monthly_analyst/            # P2 cookbook (load_period -> rollups -> budget_variance -> anomalies -> draft -> lint -> publish -> report)
  knowledge_engine/           # P3 cookbook (Q&A agent + CLI)
  advisor/                    # P5 cookbook (load_context -> flag -> draft -> lint -> publish -> report)
docs/
  architecture.md             # technical architecture
  superpowers/plans/          # P1, P2, P3, P4, P5 plans
scripts/                      # setup.sh, check-egress.sh
tests/                        # 315 unit tests, all-synthetic fixtures
```

## Tests

```bash
.venv/bin/pytest tests/ --ignore=tests/statement_ingester/test_real_backfill.py
```

The integration test `test_real_backfill.py` is skipped by default; it
needs `PFH_RUN_INTEGRATION=1` and a running Ollama. All other tests
use synthetic data (no real PII).

## License

Private — not yet published. See repo owner.
