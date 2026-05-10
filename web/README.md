# personal-finance-helper · web

Local-only Next.js dashboard for the P1-P5 cookbooks. Talks to the
FastAPI shim at `http://127.0.0.1:8000` (running via
`python -m cookbooks.api`).

## Setup

```bash
cd web
pnpm install         # one-time
pnpm dev             # next dev on http://127.0.0.1:3000
```

Or use the orchestrator from the repo root:

```bash
bash scripts/dev.sh  # runs the FastAPI shim AND the Next.js dev server
```

## Production build

```bash
bash scripts/build-web.sh
cd web && pnpm start
```

## Architecture (one paragraph)

Server Components fetch `/api/*` directly from the FastAPI shim;
client components only kick in for interactive bits (Q&A chat,
recommendation accept/dismiss). Markdown bodies render via
`react-markdown` with `[[wikilinks]]` resolved to Next.js `<Link>`
elements (see `lib/wikilinks.tsx`). All routes are loopback-only — the
dev server hard-binds to `127.0.0.1`, the production server starts
with the same flag, and CSP locks the page to its own origin + the
local API. No external CDN imports at build or run time.

## Routes

| Path | Purpose |
|---|---|
| `/`                       | Dashboard (KPI tiles + open recommendations) |
| `/memos`                  | Memo list |
| `/memos/[period]`         | Rendered memo + citation sidebar |
| `/merchants`              | Merchant table with category + query filters |
| `/merchants/[id]`         | Merchant detail + recent transactions |
| `/recommendations`        | Inbox tabs (proposed/accepted/dismissed) |
| `/recommendations/[id]`   | Detail + accept/dismiss buttons |
| `/budgets`                | Budgets table with computed variance |
| `/qa`                     | Chat-style Q&A over the graph + wiki |
| `/graph`                  | Snapshot stats + link to the pyvis HTML viz |
| `/decisions/[id]`         | Decision page + replay drift status |
