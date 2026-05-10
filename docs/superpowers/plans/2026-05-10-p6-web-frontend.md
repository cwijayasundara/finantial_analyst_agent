# P6: Web Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A local-only Next.js dashboard that turns the existing P1-P5
artefacts (ledger, wiki, graph, memos, recommendations) into a navigable
GUI without weakening the privacy contract. Single-user, runs entirely
on `127.0.0.1`. No data leaves the machine; no remote analytics; no
auth (single-user assumption).

**Why now:** P1-P5 already produces all the data a UI would need
(DuckDB ledger, Markdown wiki, Kuzu graph, audit log). What's missing
is a way to interact without the CLI — accept/dismiss recommendations
in one click, browse memos with rendered wikilinks, ask Q&A questions
with streaming answers.

**Architecture:** Two-process local app.

```
┌──────────────────────────────────┐         ┌──────────────────────────┐
│  Next.js 15 (App Router)         │         │  FastAPI shim            │
│  http://127.0.0.1:3000           │  HTTP   │  http://127.0.0.1:8000   │
│  TypeScript · Tailwind · React 19│ ──────▶ │  Wraps cookbooks._shared │
│  Server + Client Components      │         │  qa_tools, actions, db   │
└──────────────────────────────────┘         └──────────────────────────┘
        ▲                                              │
        │  rendered HTML/JSON                          ▼
        │                                     ┌──────────────────┐
        │                                     │  P1-P5 data      │
        │                                     │  data/ledger.duckdb │
        │                                     │  wiki/             │
        │                                     │  graph/kuzu.db     │
        │                                     └──────────────────┘
        │
   user's browser (loopback only)
```

**Tech stack:**
- Backend shim: FastAPI 0.115 + uvicorn, Python 3.12, reuses existing
  cookbooks code (no new analytics).
- Frontend: Next.js 15 (App Router, React 19, TypeScript, Tailwind v4),
  `react-markdown` for memo rendering with `[[wikilink]]` interception,
  vis.js for the graph view (the same library pyvis already extracts).
- Build: `pnpm` for the web side, `uv` for Python (existing).
- No remote services; no telemetry.

**Privacy invariants:**
- Both processes hard-bind to `127.0.0.1`. Refuse `0.0.0.0` / public IPs.
- The FastAPI shim's `/api/qa/ask` calls `build_chat_model()` so the P3
  privacy stack (ollama-default + masker + audit log + assert_no_pii)
  applies unchanged.
- Static assets are bundled at build time; no CDN imports.
- `web/` directory has its own `.gitignore` lines for `node_modules`,
  `.next/`, `out/`, ensuring nothing accidentally lands in git.

---

## File Structure

```
cookbooks/api/__init__.py
cookbooks/api/__main__.py                # `python -m cookbooks.api` runs uvicorn on 127.0.0.1:8000
cookbooks/api/server.py                  # FastAPI app + loopback assertion
cookbooks/api/routers/memos.py
cookbooks/api/routers/merchants.py
cookbooks/api/routers/statements.py
cookbooks/api/routers/recommendations.py
cookbooks/api/routers/budgets.py
cookbooks/api/routers/decisions.py
cookbooks/api/routers/graph.py           # serves graph/snapshots/graph.jsonl as JSON
cookbooks/api/routers/qa.py              # streaming Q&A endpoint

tests/api/test_server.py
tests/api/test_memos_router.py
tests/api/test_merchants_router.py
tests/api/test_recommendations_router.py
tests/api/test_qa_router.py
tests/api/test_loopback_only.py          # explicit privacy assertion test

web/                                      # Next.js workspace root
web/.gitignore                            # node_modules, .next, out, .turbo
web/package.json                          # pnpm workspace
web/pnpm-lock.yaml
web/next.config.ts
web/tsconfig.json
web/tailwind.config.ts
web/postcss.config.js
web/biome.json                            # lint+format (replaces eslint+prettier)

web/app/layout.tsx                        # shell + nav + theme
web/app/page.tsx                          # dashboard home (period picker + KPI tiles)
web/app/memos/page.tsx                    # memo list
web/app/memos/[period]/page.tsx           # one memo, rendered Markdown + sidebar
web/app/merchants/page.tsx                # merchant table with filters
web/app/merchants/[id]/page.tsx           # one merchant + transactions + back-links
web/app/recommendations/page.tsx          # proposed inbox + accepted history
web/app/budgets/page.tsx                  # set/list/edit budgets
web/app/graph/page.tsx                    # interactive graph (vis.js)
web/app/qa/page.tsx                       # chat-style Q&A interface
web/app/decisions/[id]/page.tsx           # decision detail + replay drift status

web/lib/api.ts                            # typed fetch helpers
web/lib/wikilinks.tsx                     # [[page_id]] → Next.js <Link> component
web/lib/types.ts                          # TS types mirroring Pydantic schemas
web/components/MarkdownView.tsx           # react-markdown wrapper with wikilink + Mermaid support
web/components/MemoCard.tsx
web/components/RecommendationCard.tsx
web/components/GraphView.tsx              # vis.js + dark-mode + filters
web/components/QAChat.tsx                 # streaming response with citation chips
web/components/PeriodPicker.tsx
web/components/StatusBadge.tsx
web/components/ui/                        # shadcn-ish primitives (button, dialog, table)

scripts/dev.sh                            # spawns both servers concurrently
scripts/build-web.sh                      # production build of the Next.js app
```

---

## Task 1: FastAPI shim scaffolding

- [ ] Add to `pyproject.toml` `[project.optional-dependencies] web`:
      `fastapi>=0.115`, `uvicorn>=0.32`, `python-multipart>=0.0.10`
- [ ] `cookbooks/api/server.py`: FastAPI app, CORS allowing only
      `http://127.0.0.1:3000`, mount routers
- [ ] `__main__.py` calls `uvicorn.run(host="127.0.0.1", port=8000)`.
      Refuse non-loopback hosts via env override (`PFH_API_HOST`); raise
      `RuntimeError` if user tries `0.0.0.0`
- [ ] Health check endpoint `GET /api/health` returns
      `{"status":"ok", "host": "127.0.0.1", "build": "<git sha>"}`
- [ ] Tests: server boots, refuses non-loopback host, health endpoint
      returns expected shape

## Task 2: Read endpoints

Each returns JSON shaped to match a Pydantic model exported in
`web/lib/types.ts` (a TypeScript codegen step is overkill for v1 —
just keep them in sync manually).

- [ ] `GET /api/memos` → `[{period, page_id, updated, citations_count}]`
- [ ] `GET /api/memos/{period}` → `{frontmatter, body_md, citations: [{id, type, name?}]}`
- [ ] `GET /api/merchants?category=&q=&limit=` → paginated table rows
- [ ] `GET /api/merchants/{id}` → full frontmatter + recent transactions
      (last 50, joined to statements)
- [ ] `GET /api/statements?account=` → list with sums
- [ ] `GET /api/recommendations?status=proposed` → inbox shape
- [ ] `GET /api/recommendations/{id}` → full body + frontmatter
- [ ] `GET /api/budgets?period=` → list with computed variance
- [ ] `GET /api/decisions/{id}` → frontmatter + reconstruction via
      `replay_decision()`; surfaces drift flags
- [ ] `GET /api/graph/snapshot` → `{nodes:[…], edges:[…]}` from
      `graph/snapshots/graph.jsonl`, with optional `?type=Merchant`
      filter and `?limit=` cap
- [ ] Tests: each endpoint with a fixture workspace returning expected
      JSON shape

## Task 3: Action endpoints (HITL writes)

All wrap existing actions; they're the only writes the UI can perform.

- [ ] `POST /api/recommendations/{id}/accept` body `{actor, reason?}`
      → flips status, calls `_audit` indirectly via the existing CLI helper
- [ ] `POST /api/recommendations/{id}/dismiss` same shape
- [ ] `POST /api/budgets` body `{period, scope_type, scope_id,
      target_amount, notes?}` → `upsert_budget`
- [ ] `POST /api/merchants/merge` body `{source_merchant_id,
      target_merchant_id, reason}` → `merge_merchant_aliases`. **Returns
      a confirmation challenge first (Idempotency-Key required) so the
      UI's HITL dialog can surface a preview** before the actual merge
- [ ] `POST /api/concept-reviews/{id}/close` → flip status to `closed`
- [ ] Tests: each verb hits the action layer, Decision page emitted,
      idempotency-key replay returns 409 on second attempt

## Task 4: Q&A streaming endpoint

- [ ] `POST /api/qa/ask` body `{question, allow_writes?}` returns
      Server-Sent Events:
      ```
      data: {"event":"tool","name":"query_graph","args":{...}}
      data: {"event":"tool_result","content":[...]}
      data: {"event":"answer","content":"…"}
      ```
- [ ] Wraps `cookbooks.knowledge_engine.agent.build_qa_agent` and yields
      events as the loop iterates
- [ ] Defaults `allow_writes=False`; the merge surface goes through Task
      3's dedicated endpoint with HITL confirmation
- [ ] Tests: SSE stream emits expected event sequence with a mocked chat
      model

## Task 5: Privacy assertion test (`test_loopback_only.py`)

- [ ] Imports the FastAPI app and asserts the `lifespan` context bombs
      out when host != `127.0.0.1` / `localhost` / `::1`
- [ ] Asserts CORS is denied for `https://example.com`
- [ ] Asserts `/api/qa/ask` invokes `build_chat_model()` (so the existing
      privacy stack engages) and never instantiates `ChatOpenAI` directly
- [ ] Marked `@pytest.mark.privacy` so it can be filtered as a focused
      smoke

## Task 6: Next.js scaffolding

- [ ] `pnpm create next-app web --ts --tailwind --eslint --app
      --src-dir false --import-alias "@/*"`
- [ ] Pin Node ≥ 22 in `web/package.json` engines
- [ ] Replace eslint with `biome` (faster, single tool); add
      `pnpm fmt` and `pnpm lint` scripts
- [ ] `web/.gitignore`: `node_modules/`, `.next/`, `out/`, `.turbo/`,
      `.env.local`
- [ ] `next.config.ts`: dev proxy `/api/*` → `http://127.0.0.1:8000/api/*`,
      strict mode on, CSP locked to `'self'`
- [ ] Tailwind v4 with a tiny custom palette matching the CLI's Rich
      theme (greens for positive variance, ambers for over-budget, etc.)
- [ ] Layout shell with nav: Dashboard · Memos · Merchants ·
      Recommendations · Budgets · Q&A · Graph

## Task 7: MarkdownView + wikilink resolver

- [ ] `web/lib/wikilinks.tsx`: regex `/\[\[([a-z0-9_]+)\]\]/g` →
      `<Link href="/<page-route>">{display}</Link>`. Routes:
      `merchant_X` → `/merchants/X`, `memo_2025_04` → `/memos/2025_04`,
      `rec_2025_04_X` → `/recommendations/X`, etc.
- [ ] `web/components/MarkdownView.tsx`: `react-markdown` with the
      wikilink rehype plugin + Mermaid renderer (memos contain
      Mermaid blocks for variance charts)
- [ ] Tests: snapshot test with a sample memo body covering all wikilink
      shapes

## Task 8: Memo browser

- [ ] `app/memos/page.tsx`: server component, lists every memo
      chronologically with KPI tiles (txn count, top category, anomaly
      count). Period picker scopes the inbox.
- [ ] `app/memos/[period]/page.tsx`: server component renders
      MarkdownView; right-hand sidebar lists `cites` with hoverable
      previews (HoverCard); "Open in Obsidian" link uses
      `obsidian://open?vault=…&file=memos/memo_<period>`
- [ ] Tests: e2e (Playwright optional) — for v1, just snapshot tests

## Task 9: Merchant browser

- [ ] Table page with filter pills (category, has-budget) and
      free-text search
- [ ] Per-merchant detail: spend chart (last 6 months bar chart via
      Recharts), recent transactions table, alias list, "Suggested
      merges" panel populated by `merge_merchants` candidates (reuse the
      `dedupe-merchants --dry-run --llm` rule set)
- [ ] HITL merge dialog: shows a preview of repointed transactions
      before the user confirms

## Task 10: Recommendation inbox

- [ ] `app/recommendations/page.tsx`: tabs `Proposed | Accepted | Dismissed`
- [ ] `RecommendationCard` with kind-specific iconography, citations as
      pill links, and Accept/Dismiss buttons (POST to Task 3 endpoints)
- [ ] After action: optimistic update + revalidate; show a toast that
      the Decision page was emitted (clickable to drill into it)

## Task 11: Q&A chat page

- [ ] Single-page chat UI; `QAChat` component pumps SSE from
      `POST /api/qa/ask`
- [ ] Tool-call events render as collapsible "Used: query_graph" cards
- [ ] Citation chips parse `[[wikilink]]` from the answer and render as
      Next.js Links
- [ ] `Refused` events surface as a yellow info toast; explanation
      includes "use the merge button on the merchants page"

## Task 12: Graph view

- [ ] `app/graph/page.tsx`: client component embeds vis.js using the
      JSONL snapshot fetched from `/api/graph/snapshot`
- [ ] Type filter (Merchant/Statement/Memo/Decision/Budget),
      transaction-aggregation toggle (default ON: collapses
      Statement→Merchant edges to weighted)
- [ ] Click a node → opens the corresponding wiki page in a side panel
      via the same MarkdownView

## Task 13: Budget management page

- [ ] List view with variance badges (over/under/on_track)
- [ ] Add-budget dialog (period picker + scope picker + amount input)
      → POST to Task 3 endpoint
- [ ] Edit / delete actions with HITL confirmation

## Task 14: Decision detail + replay

- [ ] `app/decisions/[id]/page.tsx`: shows the Decision page contents
      plus a "Replay" button that calls `GET /api/decisions/{id}` (which
      runs `replay_decision()` server-side) and renders the drift status
      inline (live_pages_at_ts, prior_decisions_count, fingerprint
      drift Yes/No badges)

## Task 15: Dev scripts + docs

- [ ] `scripts/dev.sh`: spawns FastAPI + Next.js concurrently with
      `concurrently` (or `wait-on` + plain shell). Both bind to
      127.0.0.1; both die on Ctrl-C.
- [ ] `scripts/build-web.sh`: production `next build` + `next start`
      bound to 127.0.0.1
- [ ] Update top-level README:
  - Add `python -m cookbooks.api` and `cd web && pnpm dev` to the
    daily-workflow section
  - Update the "Frontend" section: now points to `web/README.md`
- [ ] `web/README.md`: setup, dev workflow, build, environment vars

## Task 16: Acceptance + tag

- [ ] All P1-P5 tests pass (315) + ≥40 new API tests + Playwright smoke
      tests for the 4 main pages (memos, recommendations, qa, graph)
- [ ] `bash scripts/check-egress.sh` still passes (no new outbound
      hosts)
- [ ] Local roundtrip: ingest → analyse → recommend → open the web
      dashboard → accept a recommendation → confirm Decision page in
      `wiki/decisions/`
- [ ] Tag: `p6-frontend`

---

## Out of scope (defer)

- **Authentication / multi-user**: single-user assumption holds.
- **Mobile**: desktop browser only for v1; responsive enough but no
  PWA / offline mode.
- **Export to PDF / spreadsheet**: the wiki + DuckDB are already
  consumable; export is a downstream tool.
- **Real-time updates**: pages revalidate on action; no WebSocket
  push for new transactions.
- **Theming beyond light/dark**: ship dark + light, nothing else.
- **Tauri/Electron packaging**: stays browser-based for v1.

## Risks

| Risk | Mitigation |
|---|---|
| Adding a frontend opens new attack surface | Hard loopback bind on both servers; CSP locked; no remote dependencies at build time |
| Dual-process dev workflow is painful | `scripts/dev.sh` uses `concurrently`; both servers reload on file change |
| Next.js Server Components calling Python via HTTP each request is slow | Add an in-memory cache for hot endpoints (memos list, merchants list); revalidate on action POST |
| Q&A streaming (SSE) gotchas across `localhost` proxies | Test in dev (Next.js dev proxy) and prod (Next.js standalone) — fall back to polling if SSE breaks |
| `react-markdown` security: rendering arbitrary wiki content | Sanitise HTML; the only sources are our own pipeline; no user-uploaded markdown |
| Build size growing | Bundle analyzer in CI; cap initial JS at 200 KB gzipped |

## Definition of Done for the whole personal-finance-helper after P6

- P1 + P2 + P3 + P4 + P5 + P6 tags shipped.
- One unified workflow: `python -m cookbooks.statement_ingester backfill
  sources/` then `python -m cookbooks.monthly_analyst backfill-memos
  …` then `bash scripts/dev.sh` opens a fully-functional local
  dashboard.
- Privacy contract intact: only `127.0.0.1` traffic; opt-in remote LLM
  preserved end-to-end.
- Obsidian remains a first-class alternate UI for the same wiki.
