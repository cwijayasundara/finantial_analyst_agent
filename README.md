# Personal Finance Helper (codename *openclaw*)

Privacy-first, locally-hosted personal finance analyser, advisor, and
budget manager. Ingests PDF bank and credit-card statements, normalises
into a typed graph + ledger, and exposes a multi-cookbook agentic
surface for monthly memos, Q&A, budget tracking, and actionable
recommendations.

**Status:** P1–P9 all shipped. **443 unit tests passing.** Architecture
is documented in [`docs/architecture.md`](docs/architecture.md).

## What it does

| Cookbook / module | Phase | What it produces |
|---|---|---|
| [`statement_ingester`](cookbooks/statement_ingester/) | P1 | DuckDB ledger + Wiki pages from your PDFs |
| [`monthly_analyst`](cookbooks/monthly_analyst/) | P2 | One Markdown memo per period with rollups + anomalies + budget variance + forecast + goals |
| [`knowledge_engine`](cookbooks/knowledge_engine/) | P3 | Q&A agent over the graph + wiki, with `[[wikilink]]` citations |
| Budget management | P4 | `Budget` ObjectType + `set_budget` action; surfaces in the analyst's memos |
| [`advisor`](cookbooks/advisor/) | P5 | Recommendations (cancel sub, adjust budget, investigate anomaly, forecast overshoot, goal off-track, credit payoff) + concept-review queue |
| FastAPI + Next.js dashboard | P6 | Local-only web UI under `cookbooks/api/` + `web/` |
| Goals + Net Worth | P7 | `Goal` + `NetWorthSnapshot` ObjectTypes, plan-mode advice on debt payoff |
| Forecasting | P8 | `forecast_category` + per-category sparklines + `forecast_overshoot` advisor kind |
| Eval framework | P9 | YAML-driven regression suites under `cookbooks/<name>/evals/` |

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

# 2. (Optional) Set or update budgets / goals / net-worth snapshots
python -m cookbooks.statement_ingester budget set 2025_04 category groceries 200
python -m cookbooks.statement_ingester budget list 2025_04
python -m cookbooks.statement_ingester goal add "Emergency fund" 8000 2025-09 savings_account a_savings
python -m cookbooks.statement_ingester goal progress 2025_04
python -m cookbooks.statement_ingester networth snapshot 2025_04

# 3. Generate the monthly memo for a period (includes forecast + goals)
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

## Web frontend

A local-only Next.js + FastAPI dashboard ships in **P6** under `web/`
+ `cookbooks/api/`. Both processes hard-bind to `127.0.0.1`. CSP locks
the page to its own origin and the local API; no CDN imports; no
external network at build or run time.

```bash
# One-time
cd web && pnpm install

# Both servers in one shell (Ctrl-C kills both)
bash scripts/dev.sh

# Or split:
python -m cookbooks.api          # http://127.0.0.1:8000
cd web && pnpm dev               # http://127.0.0.1:3000

# Production build
bash scripts/build-web.sh
cd web && pnpm start
```

Routes: `/` (dashboard), `/memos`, `/memos/[period]`, `/merchants`,
`/merchants/[id]`, `/recommendations`, `/recommendations/[id]`,
`/budgets`, `/goals`, `/networth`, `/forecast`, `/qa`, `/graph`,
`/decisions/[id]`. See [`web/README.md`](web/README.md) for the layout
and [`docs/superpowers/plans/2026-05-10-p6-web-frontend.md`](docs/superpowers/plans/2026-05-10-p6-web-frontend.md)
for the design.

The Q&A endpoint calls `build_chat_model()` so the privacy stack
(masker / denylist / `assert_no_pii` guard / audit log) applies
unchanged.

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
  statement_ingester/         # P1 cookbook (parse -> validate -> upsert -> categorise -> recurring -> compile -> report); also hosts budget/goal/networth CLIs
  monthly_analyst/            # P2 + P7 + P8 cookbook (load_period -> rollups -> budget_variance -> forecast -> anomalies -> goals -> networth -> draft -> lint -> publish -> report)
  knowledge_engine/           # P3 cookbook (Q&A agent + CLI)
  advisor/                    # P5 + P7 + P8 cookbook (load_context -> flag -> draft -> lint -> publish -> report)
  api/                        # P6 FastAPI shim — routers under cookbooks/api/routers/
eval/                         # P9 eval framework (runner, matchers, adapters, reporter)
web/                          # P6 Next.js dashboard
docs/
  architecture.md             # technical architecture
  superpowers/specs/          # design spec
  superpowers/plans/          # P1 - P9 plans
scripts/                      # setup.sh, dev.sh, build-web.sh, check-egress.sh
tests/                        # 443 unit + eval tests, all-synthetic fixtures
```

## Test the app end-to-end

A 60-second smoke test against your own statements:

```bash
# 1. Verify the unit + eval suite is green (synthetic fixtures, no real data)
.venv/bin/pytest tests/ --ignore=tests/statement_ingester/test_real_backfill.py

# 2. Ingest your PDFs (idempotent — re-runs skip in seconds)
.venv/bin/python -m cookbooks.statement_ingester backfill sources/

# 3. Produce a memo for the most recent full month
.venv/bin/python -m cookbooks.monthly_analyst analyse 2026-04
open wiki/memos/memo_2026_04.md          # or read in any Markdown viewer

# 4. Get recommendations and forecasts
.venv/bin/python -m cookbooks.advisor recommend 2026-04
.venv/bin/python -m cookbooks.statement_ingester goal progress 2026_04

# 5. Boot the dashboard (binds 127.0.0.1 only)
bash scripts/dev.sh
# Then open:
#   http://127.0.0.1:3000/          — overview
#   http://127.0.0.1:3000/memos     — generated monthly memos
#   http://127.0.0.1:3000/forecast  — sparklines per category
#   http://127.0.0.1:3000/qa        — natural-language Q&A
#   http://127.0.0.1:3000/graph     — interactive graph explorer
```

If you don't have PDFs handy, every cookbook also runs against the
synthetic fixtures used by the test suite — `pytest tests/eval -m eval`
exercises the full memo + advisor + forecast pipeline in under 2s.

## Tests

```bash
.venv/bin/pytest tests/ --ignore=tests/statement_ingester/test_real_backfill.py
```

The integration test `test_real_backfill.py` is skipped by default; it
needs `PFH_RUN_INTEGRATION=1` and a running Ollama. All other tests
use synthetic data (no real PII).

### Regression evals (P9)

YAML-driven suites under `cookbooks/<name>/evals/` exercise each
cookbook end-to-end with synthetic fixtures and deterministic
assertions. Run just the evals:

```bash
.venv/bin/pytest tests/eval -m eval
```

A markdown summary lands at `eval/out/report.md` (gitignored). Add a
new case by editing the suite YAML — no Python required. LLM-as-judge
grading is opt-in and skips automatically when the judge model
(`ollama:qwen3:14b`) isn't pulled.

## License

Private — not yet published. See repo owner.
