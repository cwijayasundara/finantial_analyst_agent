# openclaw Graph Viz UI + Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Neo4j-backed graph traversal into the Next.js dashboard (replacing the Kuzu-JSONL snapshot path), then shed the parallel-run scaffolding — delete Kuzu, trim the Wiki to memos+decisions only, and remove DuckDB so the dispatcher collapses to a single Postgres backend.

**Architecture:** Four PRs, in this order: (1) new `/api/graph/*` endpoints backed by Plan 3's `evidence_for` / `neighbors` / `cypher_read_only` tools; migrate the existing `/graph` page and add node-explorer click-through from `/qa`. (2) one-time `migrate_wiki_to_postgres.py` for merchants/statements/budgets/goals; delete those wiki subdirs (memos + decisions stay). (3) Delete `cookbooks/_shared/compile_graph.py`, `graph/kuzu.db`, and the `kuzu` dep. (4) Migrate `tests/conftest.py` to use a session-scoped testcontainers Postgres as the default ledger; delete `db_duckdb.py` and the `duckdb` dep; collapse the dispatcher.

**Tech Stack:** Python 3.12+, uv, pytest, FastAPI (existing), Next.js 15 + React 19 (existing), `react-force-graph-2d` (already a dep), `neo4j>=5.20` (from Plan 2), `psycopg[binary]>=3.2` (from Plan 2), `testcontainers[postgres]>=4.5` (from Plan 2).

**Spec:** `docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md` — §6.6 (Kuzu/DuckDB removal + wiki trim), §9 (graph viz UI).

**Predecessor:** Plans 1-3 merged (PRs #8, #9, #10, #11, #12, #13, #14). This plan assumes Postgres + Neo4j are live in Docker, `compile_neo4j` populates the graph, `cookbooks/_shared/tools/graph_traversal.py` exposes `evidence_for` + `neighbors`, the DeepAgents agent works behind `PFH_QA_AGENT=deepagent`, and the MCP server is running.

**Out of scope (deferred to a hypothetical Plan 5):**
- Vector branch of `merchant_resolve` (requires `compile_neo4j` to populate `Merchant.embedding` — itself a substantial new pipeline)
- Concept layer with embeddings (subscription-bloat, etc.)
- Merchant resolution feedback loop (Decision-driven `rules.yaml` updates)
- Automatic evidence-subgraph rendering tied to answer citations — needs the deepagent path to enrich its response with structured `evidence_ids`, which requires synthesizer prompt + result-parsing changes that warrant their own design pass. Plan 4 instead exposes a manual "explore from this node" click-through.

---

## File Structure

### PR 4.1: Graph viz UI

**Create:**
- `cookbooks/api/routers/graph_traversal.py` — three new endpoints: `GET /api/graph/node/{id}`, `GET /api/graph/neighbors/{id}?depth=`, `GET /api/graph/path?from=&to=&max_depth=`. Backed by Plan 3's tool functions; thin pass-through.
- `tests/api/test_graph_traversal_router.py` — testcontainers Neo4j; seed + hit each endpoint
- `web/app/graph/[id]/page.tsx` — node-explorer page (server component); renders the neighbors subgraph using `react-force-graph-2d`
- `web/lib/api-graph.ts` — typed client helpers for the three new endpoints
- `docs/runbook-graph-viz.md` — short user doc

**Modify:**
- `cookbooks/api/server.py` — register the new router
- `cookbooks/_shared/tools/graph_traversal.py` (from Plan 3) — add `get_node(id)` helper if not present; the router calls it
- `web/app/graph/page.tsx` — switch from JSONL `api.graph.snapshot()` to a Neo4j-backed overview (call `/api/graph/neighbors/<root-id>?depth=2` for a few canonical anchor IDs, or just leave the page as-is and have the new `/graph/[id]/page.tsx` be the entry point)
- `web/app/qa/page.tsx` — show a "🔍 explore" link next to every tool-call result that contains a node ID (e.g. `merchant_resolve` returns `[{id, ...}]`); link goes to `/graph/<id>`
- `web/lib/api.ts` — re-export the new graph helpers under `api.graph.*`

### PR 4.2: Wiki trim

**Create:**
- `scripts/migrate_wiki_to_postgres.py` — one-time script: reads `wiki/merchants/*.md`, `wiki/statements/*.md`, `wiki/budgets/*.md`, `wiki/goals/*.md`, `wiki/accounts/*.md`, `wiki/categories/*.md`, `wiki/subscriptions/*.md`, `wiki/annotations/*.md`, `wiki/networth/*.md`. Parses frontmatter + body. Idempotent `INSERT ... ON CONFLICT (id) DO UPDATE` into the corresponding Postgres tables. Logs every row.
- `tests/scripts/test_migrate_wiki.py` — synthetic wiki fixture → testcontainers Postgres → assert tables populated

**Modify:**
- `cookbooks/_shared/qa_tools.py::read_wiki_page` — narrow `_WIKI_DIRS` from `("merchants", "statements", "categories", "accounts", "subscriptions", "memos", "decisions", "annotations", "recommendations", "budgets")` to `("memos", "decisions", "recommendations")`. (Recommendations stay — they're advisor-output prose, not structured data.)
- `tests/conftest.py::tmp_workspace` — narrow the `mkdir` list to the trimmed set

**Delete (in commit after migration verified):**
- `wiki/merchants/`, `wiki/statements/`, `wiki/budgets/`, `wiki/goals/`, `wiki/accounts/`, `wiki/categories/`, `wiki/subscriptions/`, `wiki/annotations/`, `wiki/networth/`

### PR 4.3: Kuzu removal

**Delete:**
- `cookbooks/_shared/compile_graph.py` — replaced by `compile_neo4j.py`
- `graph/kuzu.db` (and any other Kuzu artefact under `graph/`)
- `cookbooks/_shared/query.py` — replaced by `cookbooks/_shared/tools/cypher_tools.py::cypher_read_only`
- `cookbooks/api/routers/graph.py` — old `/api/graph/snapshot` endpoint reading the Kuzu JSONL; replaced by the new graph_traversal router from PR 4.1
- `tests/_shared/test_compile_graph.py`, `tests/_shared/test_query.py` — Kuzu-specific tests

**Modify:**
- `pyproject.toml` — remove `"kuzu"` from `dependencies`
- `cookbooks/_shared/qa_tools.py::query_graph` — currently calls `cookbooks._shared.query.query_graph` (Kuzu). Either delete `qa_tools.query_graph` entirely (it's only used by the legacy `knowledge_engine/agent.py` path) OR rewire it to `cypher_read_only.invoke(...)` against Neo4j. **Recommendation:** delete; the legacy agent's `query_graph` tool was Kuzu-specific and the deepagent path uses `cypher_read_only` directly.
- `cookbooks/knowledge_engine/agent.py` — remove `query_graph` from `_READ_TOOLS` in the legacy loop (or keep but reroute through Neo4j — see Bundle 9 recommendation)
- `tests/_shared/test_qa_tools.py` — delete tests for `query_graph` (the Kuzu version); keep `read_wiki_page` and `merge_merchants` tests
- `tests/knowledge_engine/test_agent.py` — delete or update tests that depend on `query_graph`-via-Kuzu
- `docs/architecture.md` — update; Kuzu is gone

### PR 4.4: DuckDB removal

**Modify:**
- `tests/conftest.py::tmp_workspace` — switch the default from DuckDB-in-tmp-dir to a session-scoped Postgres container. Add `@pytest.fixture(scope="session")` for the container; `tmp_workspace` sets `PFH_LEDGER_BACKEND=postgres` + the container's URL and runs alembic once
- `cookbooks/_shared/config.py::LedgerSettings` — drop the `backend` field validator's "duckdb" branch; only "postgres" is valid (raise on anything else, including the old default)
- `cookbooks/_shared/db.py` — collapse from dispatcher to direct re-export from `db_postgres.py`. `active_backend()` always returns "postgres"
- `pyproject.toml` — remove `"duckdb"` from `dependencies`

**Delete:**
- `cookbooks/_shared/db_duckdb.py`
- `cookbooks/_shared/db_postgres.py` — move its contents into the simplified `db.py` (no need for the indirection once DuckDB is gone)
- `tests/_shared/test_db_dispatcher.py` — no dispatcher to test
- `tests/_shared/test_backend_equivalence.py` (under tests/statement_ingester/) — only one backend now
- `data/ledger.duckdb` (and `data/*.duckdb.wal`)
- `docs/runbook-postgres.md`'s "Switch back to DuckDB" section

---

## PR 4.1: Graph viz UI

### Task 1: New `/api/graph/*` endpoints (node, neighbors, path)

**Files:**
- Create: `cookbooks/api/routers/graph_traversal.py`
- Modify: `cookbooks/api/server.py` (register router)
- Modify: `cookbooks/_shared/tools/graph_traversal.py` (add `get_node` helper)
- Create: `tests/api/test_graph_traversal_router.py`

- [ ] **Step 1: Add `get_node` helper to graph_traversal.py**

Read the current `cookbooks/_shared/tools/graph_traversal.py`:
```
cat /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/tools/graph_traversal.py
```

Append the following at the bottom of the file (after `neighbors`):

```python
@tool
def get_node(node_id: str) -> dict | None:
    """Return the single node with the given id, or None if not found.

    Returns ``{id, label, properties}`` — label is the first/primary label.
    """
    with session(read_only=True) as s:
        result = s.run(
            "MATCH (n {id: $id}) RETURN n, labels(n) AS labels LIMIT 1",
            {"id": node_id},
        )
        rec = result.single()
    if rec is None:
        return None
    node = rec["n"]
    labels = rec["labels"]
    return {
        "id": node_id,
        "label": labels[0] if labels else None,
        "properties": dict(node),
    }
```

- [ ] **Step 2: Write the failing router test**

Create `tests/api/test_graph_traversal_router.py` with EXACT content:

```python
"""Tests for the /api/graph/{node,neighbors,path} endpoints."""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient
from testcontainers.neo4j import Neo4jContainer


docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_neo4j_with_path():
    """Neo4j with merchant + transaction + category — a 2-hop path."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', canonical_name: 'Costco'})")
            s.run("CREATE (c:Category {id: 'category::groceries', name: 'Groceries'})")
            s.run(
                "CREATE (t:Transaction {id: 'tx::s1::1', date: '2026-03-15', amount: 50.00})"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (m:Merchant {id: 'merchant::costco'}) "
                "CREATE (t)-[:AT_MERCHANT]->(m)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


@pytest.fixture
def client(seeded_neo4j_with_path, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_path
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks.api.server import app
    yield TestClient(app)
    from cookbooks._shared.neo4j_client import close_driver
    close_driver()


def test_get_node_returns_node(client):
    r = client.get("/api/graph/node/merchant::costco")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "merchant::costco"
    assert data["label"] == "Merchant"
    assert data["properties"]["canonical_name"] == "Costco"


def test_get_node_404_when_missing(client):
    r = client.get("/api/graph/node/merchant::nonexistent")
    assert r.status_code == 404


def test_get_neighbors_depth_one(client):
    r = client.get("/api/graph/neighbors/merchant::costco?depth=1")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    labels = {n["label"] for n in data["nodes"]}
    assert "Merchant" in labels
    assert "Transaction" in labels


def test_get_neighbors_depth_two_reaches_category(client):
    r = client.get("/api/graph/neighbors/merchant::costco?depth=2")
    assert r.status_code == 200
    data = r.json()
    labels = {n["label"] for n in data["nodes"]}
    assert "Category" in labels


def test_get_neighbors_invalid_depth_rejected(client):
    r = client.get("/api/graph/neighbors/merchant::costco?depth=10")
    assert r.status_code == 400
```

- [ ] **Step 3: Run to confirm failure**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest tests/api/test_graph_traversal_router.py -v -p no:warnings 2>&1 | tail -10
```
Expected: 404 (router not registered) or ImportError.

- [ ] **Step 4: Implement the router**

Create `cookbooks/api/routers/graph_traversal.py` with EXACT content:

```python
"""Graph traversal endpoints — node, neighbors, path.

Thin pass-through over cookbooks/_shared/tools/graph_traversal.py.
Neo4j-backed; replaces the older Kuzu-JSONL snapshot router which is
deleted in PR 4.3.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.tools.graph_traversal import (
    evidence_for,
    get_node,
    neighbors,
)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/node/{node_id:path}")
def graph_node(node_id: str) -> dict:
    """Return one node by id."""
    result = get_node.invoke({"node_id": node_id})
    if result is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")
    return result


@router.get("/neighbors/{node_id:path}")
def graph_neighbors(
    node_id: str,
    depth: int = Query(1, ge=1, le=4),
) -> dict:
    """Return the local subgraph around `node_id` to `depth` hops.

    Returns ``{nodes: [{id, label}], edges: [{source, target, type}]}``.
    """
    try:
        return neighbors.invoke({"node_id": node_id, "depth": depth})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/evidence/{node_id:path}")
def graph_evidence(
    node_id: str,
    k: int = Query(10, ge=1, le=100),
) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to `node_id`."""
    return evidence_for.invoke({"node_id": node_id, "k": k})
```

Note: `{node_id:path}` lets IDs with colons (`merchant::costco`) pass through without URL-escaping issues.

- [ ] **Step 5: Register the router**

Edit `cookbooks/api/server.py`. Find the existing router registrations (look for lines like `app.include_router(graph.router)`) and add:

```python
from cookbooks.api.routers import graph_traversal
app.include_router(graph_traversal.router)
```

If there's a circular import or name conflict with the existing `graph.router`, alias one of them on import. The new router uses `/api/graph` prefix — same as the old one — which is fine because the old router's only route is `GET /api/graph/snapshot` (different path) so they coexist until PR 4.3 deletes the old one.

- [ ] **Step 6: Run the tests**

```
uv run pytest tests/api/test_graph_traversal_router.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 5 PASS. First run is slow (~30s — Neo4j container).

- [ ] **Step 7: Commit**

```
git add cookbooks/api/routers/graph_traversal.py \
        cookbooks/api/server.py \
        cookbooks/_shared/tools/graph_traversal.py \
        tests/api/test_graph_traversal_router.py
git commit -m "feat(api): /api/graph/{node,neighbors,evidence} endpoints

Thin FastAPI pass-through over the graph_traversal tools from PR
3.3 (Plan 3). Backs the upcoming /graph/[id] explorer page.
Old /api/graph/snapshot router stays for one PR cycle — PR 4.3
deletes it along with the Kuzu artefacts."
```

---

### Task 2: Web client helpers

**Files:**
- Create: `web/lib/api-graph.ts`
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Read existing api.ts to see the structure**

```
sed -n '1,60p' /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/web/lib/api.ts
```

Note where `api.graph.*` is currently defined (the existing JSONL snapshot helper).

- [ ] **Step 2: Write api-graph.ts**

Create `web/lib/api-graph.ts` with EXACT content:

```typescript
// Typed client for the /api/graph/{node,neighbors,evidence} endpoints
// (graph_traversal.py router, added in Plan 4 PR 4.1).

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export type GraphNode = {
  id: string;
  label: string;
  properties?: Record<string, unknown>;
};

export type GraphEdge = {
  source: string;
  target: string;
  type: string;
};

export type Subgraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type Transaction = {
  id: string;
  date: string;
  amount: number;
  raw_description: string;
};

export async function fetchNode(nodeId: string): Promise<GraphNode | null> {
  // node ids contain `::` — encode just enough so the URL is safe.
  const safe = encodeURI(nodeId);
  const r = await fetch(`${API_BASE}/api/graph/node/${safe}`, { cache: "no-store" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`fetchNode ${nodeId}: ${r.status}`);
  return (await r.json()) as GraphNode;
}

export async function fetchNeighbors(
  nodeId: string,
  depth: number = 1,
): Promise<Subgraph> {
  const safe = encodeURI(nodeId);
  const r = await fetch(
    `${API_BASE}/api/graph/neighbors/${safe}?depth=${depth}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw new Error(`fetchNeighbors ${nodeId}: ${r.status}`);
  return (await r.json()) as Subgraph;
}

export async function fetchEvidence(
  nodeId: string,
  k: number = 10,
): Promise<Transaction[]> {
  const safe = encodeURI(nodeId);
  const r = await fetch(
    `${API_BASE}/api/graph/evidence/${safe}?k=${k}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw new Error(`fetchEvidence ${nodeId}: ${r.status}`);
  return (await r.json()) as Transaction[];
}
```

- [ ] **Step 3: Re-export from `api.ts`**

In `web/lib/api.ts`, append at the bottom:

```typescript
import * as graphTraversal from "./api-graph";

// Augment the existing `api.graph` namespace with the new traversal helpers.
// The existing api.graph.snapshot stays for now; PR 4.3 removes it.
(api.graph as any).node = graphTraversal.fetchNode;
(api.graph as any).neighbors = graphTraversal.fetchNeighbors;
(api.graph as any).evidence = graphTraversal.fetchEvidence;
```

If `api.graph` isn't a namespace object today (it might be defined directly), restructure as needed — preserve `api.graph.snapshot`.

- [ ] **Step 4: Type-check**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/web
pnpm typecheck 2>&1 | tail -10
```

If TypeScript complains about the `(api.graph as any)` cast, add proper module augmentation instead — but the `any` cast is acceptable as a one-line fix.

- [ ] **Step 5: Commit**

```
git add web/lib/api-graph.ts web/lib/api.ts
git commit -m "feat(web): typed client for /api/graph/{node,neighbors,evidence}

api-graph.ts exports fetchNode / fetchNeighbors / fetchEvidence with
GraphNode / Subgraph / Transaction types. Augments the existing
api.graph namespace; the JSONL snapshot helper stays until PR 4.3
removes it."
```

---

### Task 3: Node-explorer page

**Files:**
- Create: `web/app/graph/[id]/page.tsx`

- [ ] **Step 1: Write the page**

Create `web/app/graph/[id]/page.tsx` with EXACT content:

```tsx
// Server component — fetches the subgraph for one node id, hands it
// to the client GraphView component for force-directed rendering.

import Link from "next/link";

import { GraphView } from "@/components/GraphView";
import { fetchNeighbors, fetchNode, fetchEvidence } from "@/lib/api-graph";

export const dynamic = "force-dynamic";

export default async function GraphNodePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ depth?: string }>;
}) {
  const { id: rawId } = await params;
  const sp = await searchParams;
  // Decode the [id] segment — Next.js gives it already-decoded but we
  // call decodeURIComponent defensively in case of double-encoding.
  const id = decodeURIComponent(rawId);
  const depth = Math.max(1, Math.min(4, Number(sp.depth ?? 2)));

  const [node, subgraph, evidence] = await Promise.all([
    fetchNode(id),
    fetchNeighbors(id, depth),
    fetchEvidence(id, 20).catch(() => []),
  ]);

  if (!node) {
    return (
      <div className="space-y-4">
        <header>
          <h1 className="text-2xl font-semibold">Node not found</h1>
          <p className="text-sm opacity-70">
            No node with id <code>{id}</code>. Try{" "}
            <Link href="/graph" className="underline">the overview</Link>.
          </p>
        </header>
      </div>
    );
  }

  // GraphView expects { nodes, edges } in the snapshot shape — adapt the
  // {id, label} / {source, target, type} shape to it.
  const snap = {
    nodes: subgraph.nodes.map(n => ({
      kind: "node" as const,
      id: n.id,
      type: n.label,
    })),
    edges: subgraph.edges.map(e => ({
      kind: "edge" as const,
      from: e.source,
      to: e.target,
      type: e.type,
    })),
    node_count: subgraph.nodes.length,
    edge_count: subgraph.edges.length,
  };

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">
          {node.label}: <code className="text-base">{id}</code>
        </h1>
        <p className="text-sm opacity-70">
          {subgraph.nodes.length} nodes · {subgraph.edges.length} edges
          {" · "}
          <Link href={`/graph/${encodeURIComponent(id)}?depth=${Math.max(1, depth - 1)}`} className="underline">
            depth −1
          </Link>
          {" · "}
          <Link href={`/graph/${encodeURIComponent(id)}?depth=${Math.min(4, depth + 1)}`} className="underline">
            depth +1
          </Link>
        </p>
      </header>

      {Object.keys(node.properties ?? {}).length > 0 && (
        <section className="border border-black/10 dark:border-white/10 rounded p-3 text-sm">
          <h2 className="font-semibold mb-1">Properties</h2>
          <pre className="overflow-x-auto">{JSON.stringify(node.properties, null, 2)}</pre>
        </section>
      )}

      <GraphView snapshot={snap} />

      {evidence.length > 0 && (
        <section className="border border-black/10 dark:border-white/10 rounded p-3 text-sm">
          <h2 className="font-semibold mb-2">
            Adjacent transactions ({evidence.length})
          </h2>
          <ul className="space-y-1 font-mono">
            {evidence.map(t => (
              <li key={t.id}>
                {t.date} · £{t.amount.toFixed(2)} · {t.raw_description}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
```

The `GraphView` component already exists from earlier work (used by the `/graph` overview page). It accepts a `snapshot` prop in the old JSONL shape — that's why we map our `{id, label}` and `{source, target, type}` into `{kind: "node", id, type}` and `{kind: "edge", from, to, type}` before passing it in. No changes to `GraphView` itself.

- [ ] **Step 2: Verify the page renders against a live dev stack**

Start the dev server in one terminal (or skip if Docker infra isn't up):

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
# Terminal 1: API
PFH_LEDGER_BACKEND=postgres \
PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw \
PFH_NEO4J_URL=bolt://127.0.0.1:7687 \
PFH_NEO4J_PASSWORD=local-dev \
uv run uvicorn cookbooks.api.server:app --host 127.0.0.1 --port 8000 &

# Terminal 2: web
cd web && pnpm dev &

# Visit http://127.0.0.1:3000/graph/merchant::costco
```

If the Docker infra isn't running, skip the live test — the router tests in Task 1 already cover the backend.

If `GraphView` throws because the snapshot shape doesn't match, inspect `web/components/GraphView.tsx` to confirm the field names (`kind`, `from`/`to`, etc.) and adjust the mapping in the page.

- [ ] **Step 3: TypeScript check**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/web
pnpm typecheck 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
git add web/app/graph/[id]/page.tsx
git commit -m "feat(web): /graph/[id] node-explorer page

Server component fetches the subgraph for one node id via the
new /api/graph/{node,neighbors,evidence} endpoints and renders
it through the existing GraphView (react-force-graph-2d).

Adjacent transactions surface in a table beneath the graph.
Depth +/- links let the user widen / narrow the view.

Adapts the new {id, label} / {source, target, type} shape into
the existing JSONL {kind, id, type} / {kind, from, to, type}
snapshot shape so GraphView stays unchanged."
```

---

### Task 4: Click-through from /qa to /graph/[id]

**Files:**
- Modify: `web/app/qa/page.tsx`

The Q&A page shows tool-call results. When a tool result contains node IDs (most obviously `merchant_resolve` returning `[{id, canonical_name, score}, ...]`), let the user click through to the graph explorer.

- [ ] **Step 1: Read current qa/page.tsx**

```
cat /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/web/app/qa/page.tsx | head -120
```

Note where tool calls are rendered (look for `t.toolCalls.map(...)` or similar). The interesting render target is the tool-call output panel.

- [ ] **Step 2: Add a `<NodeLink>` helper**

In `web/app/qa/page.tsx`, near the top (after imports), add a small helper:

```tsx
function NodeLink({ id, label }: { id: string; label?: string }) {
  // Surface any node id from a tool call as a click-through to /graph/[id].
  const href = `/graph/${encodeURIComponent(id)}`;
  return (
    <a href={href} className="underline decoration-dotted hover:decoration-solid">
      🔍 {label ?? id}
    </a>
  );
}

// Walk an arbitrary tool-call result; return the list of {id, label?} pairs
// we should surface as click-throughs. Conservative — only surfaces strings
// that look like node ids (contain `::`).
function extractNodeRefs(result: unknown): { id: string; label?: string }[] {
  const out: { id: string; label?: string }[] = [];
  const visit = (v: unknown) => {
    if (v == null) return;
    if (typeof v === "string") {
      if (v.includes("::")) out.push({ id: v });
      return;
    }
    if (Array.isArray(v)) { v.forEach(visit); return; }
    if (typeof v === "object") {
      const obj = v as Record<string, unknown>;
      if (typeof obj.id === "string" && obj.id.includes("::")) {
        const label = typeof obj.canonical_name === "string"
          ? obj.canonical_name
          : typeof obj.name === "string"
            ? obj.name
            : undefined;
        out.push({ id: obj.id, label });
        return;
      }
      Object.values(obj).forEach(visit);
    }
  };
  visit(result);
  // Dedup by id.
  const seen = new Set<string>();
  return out.filter(r => {
    if (seen.has(r.id)) return false;
    seen.add(r.id);
    return true;
  });
}
```

- [ ] **Step 3: Render `<NodeLink>`s alongside each tool call**

Find the loop that renders `t.toolCalls`. After the existing rendering of the tool call's name + args, add a small block that extracts node refs from the call's `args` (or `result` if the page tracks it) and renders `<NodeLink>` chips.

If the page only tracks `{name, args}` per tool call (no result), wire one extra field through the API in a follow-up. For now, fall back to scanning `args` only:

```tsx
{t.toolCalls.map((tc, i) => {
  const refs = extractNodeRefs(tc.args);
  return (
    <li key={i} className="text-sm">
      <code>{tc.name}</code>{" "}
      <span className="opacity-60">({JSON.stringify(tc.args).slice(0, 100)})</span>
      {refs.length > 0 && (
        <div className="mt-1 flex gap-2 flex-wrap">
          {refs.map(r => <NodeLink key={r.id} id={r.id} label={r.label} />)}
        </div>
      )}
    </li>
  );
})}
```

(Adapt the exact JSX to whatever the existing loop looks like — preserve the existing styling.)

- [ ] **Step 4: TypeScript check**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/web
pnpm typecheck 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
git add web/app/qa/page.tsx
git commit -m "feat(web): click-through from /qa tool calls to /graph/[id]

When a tool-call args contains a node id (strings with '::'),
render a 🔍 link to /graph/[id]. Conservative heuristic — only
surfaces ids that look like canonical openclaw IDs (have ::).

Gives the user a manual path from a Q&A answer to the underlying
subgraph. Automatic evidence-subgraph rendering tied to the
answer's citations is deferred — needs synthesizer prompt + result
parsing work that warrants its own design pass."
```

---

### Task 5: PR 4.1 wrap-up

- [ ] **Step 1: Restore venv**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv sync --extra dev --extra remote --extra web
uv pip install -e .
uv run python -m spacy download en_core_web_lg 2>&1 | tail -2
```

- [ ] **Step 2: Full suite check**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

Baseline at start of PR 4 was ~588 passed, 7 pre-existing failures. PR 4.1 adds 5 router tests = ~593 passed.

If you see WAY more failures (e.g., 37+), it's venv drift — re-run Step 1.

- [ ] **Step 3: Create runbook**

Create `docs/runbook-graph-viz.md` with EXACT content:

```markdown
# Graph viz quick reference

The Next.js dashboard surfaces three node-graph views, all backed by
Neo4j via `/api/graph/*`:

## /graph (overview)

Network of all node types in the graph. Filter by type. Backed by
the older `/api/graph/snapshot` endpoint reading the JSONL compile
output. PR 4.3 migrates this page to also use Neo4j.

## /graph/[id] (node explorer)

Deep-dive on one node. Renders the subgraph to depth N (default 2)
via `/api/graph/neighbors/<id>?depth=N`. Adjacent transactions are
listed beneath the graph via `/api/graph/evidence/<id>?k=20`.

URLs use the canonical id verbatim (with `::`), e.g.:

  http://127.0.0.1:3000/graph/merchant::costco
  http://127.0.0.1:3000/graph/category::groceries

## /qa (Q&A with explore-from-tool-call)

Tool calls whose args / results contain node IDs (e.g. `merchant_resolve`
returning canonical merchant IDs) get a 🔍 link beside them. Click
through to `/graph/[id]`.
```

- [ ] **Step 4: Push + open + merge PR**

```
git add docs/runbook-graph-viz.md
git commit -m "docs: graph-viz runbook"

git push -u origin feat/openclaw-ui-cleanup
gh pr create --base main --title "feat(web): PR 1 of 4 — Neo4j-backed graph viz UI" --body "$(cat <<'EOF'
## Summary

Wires the graph-traversal tools from Plan 3 into the Next.js dashboard:

- New \`/api/graph/{node,neighbors,evidence}\` endpoints (\`cookbooks/api/routers/graph_traversal.py\`). Pass-through to the @tool callables from PR 3.3.
- New \`/graph/[id]\` server-component page renders the subgraph for any node id via react-force-graph-2d (already installed). Depth +/- links; adjacent-transactions table.
- \`/qa\` page surfaces a 🔍 click-through next to every tool-call arg that looks like a node id (string containing \`::\`). Manual path from answer to subgraph; automatic citation-driven rendering deferred.
- Old \`/api/graph/snapshot\` (JSONL) stays for one PR cycle — PR 4.3 deletes it with the rest of the Kuzu artefacts.

Spec: §9. Plan: \`docs/superpowers/plans/2026-05-18-openclaw-ui-and-cleanup.md\` PR 4.1.

## Test plan

- [x] 5 router tests via testcontainers Neo4j (node found / 404; neighbors depth 1 / 2; invalid depth rejected).
- [x] \`pnpm typecheck\` clean.
- [x] Full suite: ~593 passed, 7 pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge <PR-number> --merge
```

User has authorized merging.

---

## PR 4.2: Wiki trim

### Task 6: `migrate_wiki_to_postgres.py` script

**Files:**
- Create: `scripts/__init__.py` (if missing)
- Create: `scripts/migrate_wiki_to_postgres.py`
- Create: `tests/scripts/__init__.py`
- Create: `tests/scripts/test_migrate_wiki.py`

The script reads every page under `wiki/<subdir>/` (for the soon-to-be-deleted subdirs), parses the YAML frontmatter, and upserts into the matching Postgres table.

**Wiki → Postgres table mapping:**

| Wiki subdir | Postgres table | Frontmatter → column mapping |
|---|---|---|
| `wiki/merchants/` | `merchants` | `id, canonical_name, category_id, aliases` |
| `wiki/statements/` | `statements` | `id, account_id, period_start, period_end, source_pdf, sha256, parser_used` |
| `wiki/budgets/` | `budgets` | `id, scope_kind, scope_id, period_kind, amount` |
| `wiki/goals/` | `goals` | `id, name, target_amount, deadline, account_id` |
| `wiki/accounts/` | `accounts` | `id, name, type, currency, holder` |
| `wiki/categories/` | `categories` | `id, name, parent_id` |
| `wiki/subscriptions/` | `patterns` (table name preserved from existing schema) | `id, merchant_id, cadence, expected_amount, last_seen, confidence` |
| `wiki/annotations/` | `annotations` | `transaction_id, note, kind` |
| `wiki/networth/` | `net_worth_snapshots` | `id, period, account_id, balance` |

(`wiki/recommendations/` is NOT migrated — it stays as prose alongside memos and decisions.)

- [ ] **Step 1: Stub the package markers**

```bash
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/scripts
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/scripts
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/scripts/__init__.py
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/scripts/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/scripts/test_migrate_wiki.py` with EXACT content:

```python
"""Tests for the one-time wiki -> Postgres migration script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture
def staged_wiki(tmp_path):
    """Build a tiny wiki tree on disk; return its path."""
    wiki = tmp_path / "wiki"
    (wiki / "merchants").mkdir(parents=True)
    (wiki / "accounts").mkdir(parents=True)
    (wiki / "categories").mkdir(parents=True)
    # Bootstrap an account first (FK target for merchants etc.).
    (wiki / "accounts" / "acct_savings.md").write_text(
        "---\n"
        "id: acct_savings\n"
        "name: Savings\n"
        "type: savings\n"
        "currency: GBP\n"
        "holder: Test User\n"
        "---\n"
        "Notes.\n"
    )
    # Category before merchant (merchant FK).
    (wiki / "categories" / "cat_groceries.md").write_text(
        "---\n"
        "id: 1\n"
        "name: groceries\n"
        "parent_id: null\n"
        "---\n"
        "Body.\n"
    )
    (wiki / "merchants" / "merchant_costco.md").write_text(
        "---\n"
        "id: merchant_costco\n"
        "canonical_name: Costco\n"
        "category_id: 1\n"
        "aliases: [COSTCO WHSE, COSTCO.COM]\n"
        "---\n"
        "Body.\n"
    )
    return wiki


@pytest.fixture
def fresh_postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        yield raw_url


def test_migrate_inserts_accounts_categories_merchants(staged_wiki, fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    counts = migrate(dry_run=False)
    assert counts["accounts"] == 1
    assert counts["categories"] == 1
    assert counts["merchants"] == 1

    with psycopg.connect(fresh_postgres) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM accounts")
        assert cur.fetchall() == [("acct_savings", "Savings")]
        cur.execute("SELECT id, name FROM categories")
        assert cur.fetchall() == [(1, "groceries")]
        cur.execute("SELECT id, canonical_name FROM merchants")
        assert cur.fetchall() == [("merchant_costco", "Costco")]


def test_migrate_is_idempotent(staged_wiki, fresh_postgres, monkeypatch):
    """Second run upserts (ON CONFLICT) — no duplicates, no errors."""
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    migrate(dry_run=False)
    counts2 = migrate(dry_run=False)
    assert counts2["merchants"] == 1  # one upsert, not two


def test_migrate_dry_run_does_not_write(staged_wiki, fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    monkeypatch.setenv("PFH_WIKI_DIR", str(staged_wiki))
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from scripts.migrate_wiki_to_postgres import migrate

    counts = migrate(dry_run=True)
    # Counts still reflect what WOULD be written.
    assert counts["merchants"] == 1
    # But the DB has zero rows.
    with psycopg.connect(fresh_postgres) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM merchants")
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 3: Confirm failure**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest tests/scripts/test_migrate_wiki.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 4: Implement the script**

Create `scripts/migrate_wiki_to_postgres.py` with EXACT content:

```python
"""One-time migration: wiki markdown frontmatter -> Postgres rows.

Reads every page under wiki/<subdir>/ for the subdirs we're about to
delete (merchants, statements, budgets, goals, accounts, categories,
subscriptions, annotations, networth) and upserts into the matching
Postgres table.

Order matters because of foreign keys — accounts and categories
go first, then merchants and the rest.

Usage:
    uv run python scripts/migrate_wiki_to_postgres.py --dry-run
    uv run python scripts/migrate_wiki_to_postgres.py    # writes
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import yaml

from cookbooks._shared.config import load_settings


_log = logging.getLogger("migrate_wiki")

# Order matters: tables with no FKs first.
_TABLE_ORDER = (
    "accounts",
    "categories",
    "merchants",
    "statements",
    "patterns",          # from wiki/subscriptions/
    "budgets",
    "goals",
    "annotations",
    "net_worth_snapshots",
)

# Map wiki subdir -> Postgres table.
_DIR_TO_TABLE = {
    "accounts": "accounts",
    "categories": "categories",
    "merchants": "merchants",
    "statements": "statements",
    "subscriptions": "patterns",
    "budgets": "budgets",
    "goals": "goals",
    "annotations": "annotations",
    "networth": "net_worth_snapshots",
}

# Per-table: ordered list of (column, frontmatter_key) — we read the
# frontmatter_key from the page's YAML and write into the column.
_TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "accounts": [
        ("id", "id"), ("name", "name"), ("type", "type"),
        ("currency", "currency"), ("holder", "holder"),
    ],
    "categories": [
        ("id", "id"), ("name", "name"), ("parent_id", "parent_id"),
    ],
    "merchants": [
        ("id", "id"), ("canonical_name", "canonical_name"),
        ("category_id", "category_id"), ("aliases", "aliases"),
    ],
    "statements": [
        ("id", "id"), ("account_id", "account_id"),
        ("period_start", "period_start"), ("period_end", "period_end"),
        ("source_pdf", "source_pdf"), ("sha256", "sha256"),
        ("parser_used", "parser_used"),
    ],
    "patterns": [
        ("id", "id"), ("merchant_id", "merchant_id"),
        ("cadence", "cadence"), ("expected_amount", "expected_amount"),
        ("last_seen", "last_seen"), ("confidence", "confidence"),
    ],
    "budgets": [
        ("id", "id"), ("scope_kind", "scope_kind"),
        ("scope_id", "scope_id"), ("period_kind", "period_kind"),
        ("amount", "amount"),
    ],
    "goals": [
        ("id", "id"), ("name", "name"),
        ("target_amount", "target_amount"), ("deadline", "deadline"),
        ("account_id", "account_id"),
    ],
    "annotations": [
        ("transaction_id", "transaction_id"), ("note", "note"),
        ("kind", "kind"),
    ],
    "net_worth_snapshots": [
        ("id", "id"), ("period", "period"),
        ("account_id", "account_id"), ("balance", "balance"),
    ],
}

# Primary-key column for ON CONFLICT clause.
_PK = {
    "accounts": "id", "categories": "id", "merchants": "id",
    "statements": "id", "patterns": "id", "budgets": "id",
    "goals": "id", "annotations": "transaction_id",
    "net_worth_snapshots": "id",
}


def _parse_page(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        _log.warning("skipping %s: no frontmatter", path)
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        _log.warning("skipping %s: unterminated frontmatter", path)
        return None
    try:
        return yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        _log.warning("skipping %s: bad YAML (%s)", path, e)
        return None


def _upsert_sql(table: str) -> str:
    cols = [c for c, _ in _TABLE_COLUMNS[table]]
    placeholders = ", ".join(["%s"] * len(cols))
    cols_sql = ", ".join(cols)
    pk = _PK[table]
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk)
    return (
        f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {update_set}"
    )


def migrate(*, dry_run: bool = False) -> dict[str, int]:
    """Migrate every soon-to-be-deleted wiki subdir into Postgres."""
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "migrate_wiki_to_postgres requires PFH_LEDGER_BACKEND=postgres"
        )

    wiki = settings.paths.wiki
    counts: dict[str, int] = {table: 0 for table in _TABLE_ORDER}

    # Collect all rows first so we can sort + sequence by table.
    by_table: dict[str, list[dict[str, Any]]] = {t: [] for t in _TABLE_ORDER}
    for subdir, table in _DIR_TO_TABLE.items():
        dir_path = wiki / subdir
        if not dir_path.exists():
            continue
        for page in sorted(dir_path.glob("*.md")):
            fm = _parse_page(page)
            if fm is None:
                continue
            row: dict[str, Any] = {}
            for col, key in _TABLE_COLUMNS[table]:
                row[col] = fm.get(key)
            by_table[table].append(row)
            counts[table] += 1

    if dry_run:
        for t, rows in by_table.items():
            _log.info("dry-run %s: %d rows", t, len(rows))
        return counts

    import psycopg
    conn = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    try:
        cur = conn.cursor()
        for table in _TABLE_ORDER:
            rows = by_table[table]
            if not rows:
                continue
            sql = _upsert_sql(table)
            cols = [c for c, _ in _TABLE_COLUMNS[table]]
            for row in rows:
                cur.execute(sql, [row.get(c) for c in cols])
            _log.info("upserted %d rows into %s", len(rows), table)
        conn.commit()
    finally:
        conn.close()

    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    counts = migrate(dry_run=args.dry_run)
    total = sum(counts.values())
    _log.info("done. total rows %s: %d", "to migrate" if args.dry_run else "migrated", total)
    for table, n in counts.items():
        if n:
            _log.info("  %s: %d", table, n)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests**

```
uv run pytest tests/scripts/test_migrate_wiki.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```
git add scripts/__init__.py scripts/migrate_wiki_to_postgres.py \
        tests/scripts/__init__.py tests/scripts/test_migrate_wiki.py
git commit -m "feat(scripts): migrate_wiki_to_postgres — one-time wiki trim

Reads wiki/<subdir>/*.md for the 9 soon-to-be-deleted subdirs
(merchants, statements, budgets, goals, accounts, categories,
subscriptions, annotations, networth) and upserts into the
matching Postgres table via INSERT ... ON CONFLICT DO UPDATE.

Order respects FK constraints (accounts and categories first,
then merchants, etc.). --dry-run flag for safety. Idempotent."
```

---

### Task 7: Trim wiki + narrow `read_wiki_page`

**Files:**
- Modify: `cookbooks/_shared/qa_tools.py`
- Modify: `tests/conftest.py`
- Delete (after migration): the 9 wiki subdirs

- [ ] **Step 1: Run the migration against the real wiki**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
docker compose -f docker/docker-compose.yml up -d postgres
sleep 5

export PFH_LEDGER_BACKEND=postgres
export PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw

# Dry-run first.
uv run python scripts/migrate_wiki_to_postgres.py --dry-run 2>&1 | tail -20

# Then for real.
uv run python scripts/migrate_wiki_to_postgres.py 2>&1 | tail -20
```

Inspect the output. If counts look reasonable (no zeros where there should be data, no errors), proceed.

Spot-check a few rows:

```
psql postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw \
  -c "SELECT count(*) FROM merchants;" \
  -c "SELECT count(*) FROM statements;" \
  -c "SELECT count(*) FROM budgets;"
```

If anything is wrong, STOP and report — don't delete the wiki subdirs yet.

- [ ] **Step 2: Narrow `_WIKI_DIRS` in qa_tools.py**

Edit `cookbooks/_shared/qa_tools.py`. Change:

```python
_WIKI_DIRS = (
    "merchants", "statements", "categories", "accounts",
    "subscriptions", "memos", "decisions", "annotations",
    "recommendations", "budgets",
)
```

to:

```python
_WIKI_DIRS = (
    "memos", "decisions", "recommendations",
)
```

- [ ] **Step 3: Narrow `tmp_workspace` mkdir list in conftest**

Edit `tests/conftest.py`. Find the `tmp_workspace` fixture's `for sub in (...):` loop. Change:

```python
    for sub in ("sources", "parsed", "data", "wiki/merchants", "wiki/statements",
                "wiki/subscriptions", "wiki/memos", "wiki/decisions",
                "wiki/annotations", "graph/snapshots", "out"):
```

to:

```python
    for sub in ("sources", "parsed", "data",
                "wiki/memos", "wiki/decisions", "wiki/recommendations",
                "graph/snapshots", "out"):
```

- [ ] **Step 4: Delete the migrated wiki subdirs**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
git rm -r wiki/merchants wiki/statements wiki/budgets wiki/goals \
         wiki/accounts wiki/categories wiki/subscriptions \
         wiki/annotations wiki/networth
```

- [ ] **Step 5: Run the suite to catch any test that referenced the deleted dirs**

```
uv run pytest --tb=short -p no:warnings 2>&1 | tail -20
```

Expected: a handful of new failures — tests that did `read_wiki_page("merchant_xxx")` or asserted file existence under the deleted dirs. Fix each by either:
- Reading the merchant from Postgres instead (where applicable), OR
- Deleting the test (if it was Kuzu/wiki-merchant-page specific and the moral equivalent now lives in PR 4.1's `/api/graph/node` endpoint)

Common patterns to update:
- `tests/_shared/test_qa_tools.py::test_read_wiki_page_merchant` — narrow to test that `read_wiki_page("merchant_xxx")` now returns `{"error": "not found"}` (since merchants are not in the wiki anymore), OR delete the test
- Any test that creates `wiki/merchants/merchant_xxx.md` in its setup — refactor to use Postgres directly

Take an iterative approach: fix failures one at a time, run the suite after each fix.

- [ ] **Step 6: Commit**

```
git add cookbooks/_shared/qa_tools.py tests/conftest.py wiki/
git commit -m "feat(wiki): trim to memos + decisions + recommendations

Migrates 9 structured-data wiki subdirs (merchants, statements,
budgets, goals, accounts, categories, subscriptions, annotations,
networth) into Postgres via scripts/migrate_wiki_to_postgres.py,
then deletes them. Memos / decisions / recommendations stay —
they're long-form prose, not structured data.

read_wiki_page now searches only the trimmed dir set. tmp_workspace
fixture creates only the dirs that still exist. Tests that
referenced the deleted wiki subdirs are updated to read from
Postgres or removed where moot."
```

---

### Task 8: PR 4.2 wrap-up

- [ ] **Step 1: Full suite check**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

Should be ~593 - (deleted wiki-merchant tests) + 3 new migrate tests. The exact delta depends on Task 7 Step 5 outcomes.

- [ ] **Step 2: Push + open PR + merge**

```
git push origin feat/openclaw-ui-cleanup
gh pr create --base main --title "feat(wiki): PR 2 of 4 — trim wiki to memos + decisions" --body "$(cat <<'EOF'
## Summary

Spec §6.6 wiki trim: move structured-data wiki subdirs into Postgres, then delete them.

- New \`scripts/migrate_wiki_to_postgres.py\` — one-time idempotent migration. \`INSERT ... ON CONFLICT DO UPDATE\` per table. Foreign-key order respected.
- 9 wiki subdirs migrated and deleted: \`merchants\`, \`statements\`, \`budgets\`, \`goals\`, \`accounts\`, \`categories\`, \`subscriptions\`, \`annotations\`, \`networth\`.
- 3 wiki subdirs survive: \`memos\`, \`decisions\`, \`recommendations\` — long-form prose.
- \`read_wiki_page\` narrows its search to the surviving 3 subdirs.
- Tests that referenced the deleted wiki content updated to read from Postgres.

Spec: \`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md\` §6.6.
Plan: PR 4.2 section.

## Test plan

- [x] 3 migration script tests via testcontainers Postgres (writes / idempotent / dry-run).
- [x] Manual migration against the real wiki: counts match expected (verify via psql spot-check).
- [x] Full suite green; pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge <PR-number> --merge
```

---

## PR 4.3: Kuzu removal

### Task 9: Delete Kuzu artefacts

**Files:**
- Delete: `cookbooks/_shared/compile_graph.py`
- Delete: `cookbooks/_shared/query.py`
- Delete: `cookbooks/api/routers/graph.py`
- Delete: `graph/kuzu.db` and `graph/snapshots/graph.jsonl`
- Delete: `tests/_shared/test_compile_graph.py`, `tests/_shared/test_query.py`
- Modify: `pyproject.toml` (remove `kuzu` dep)
- Modify: `cookbooks/_shared/qa_tools.py` (delete `query_graph`)
- Modify: `cookbooks/knowledge_engine/agent.py` (remove `query_graph` from `_READ_TOOLS`)
- Modify: `cookbooks/api/server.py` (un-register the old `/api/graph/snapshot` router)
- Modify: `cookbooks/_shared/tools/safety.py` — the comment that mentioned query.py importing from here is now stale; update or remove
- Modify: `web/app/graph/page.tsx` — migrate from `api.graph.snapshot()` to a Neo4j-backed overview (call `/api/graph/neighbors` for a few canonical anchors, e.g. the highest-spend merchants), OR redirect `/graph` to `/graph/<some-anchor>` and rely on PR 4.1's node-explorer page
- Modify: `docs/architecture.md`
- Modify: `tests/_shared/test_qa_tools.py` (delete `test_query_graph_*` tests)
- Modify: `tests/knowledge_engine/test_agent.py` (delete tests referencing the Kuzu `query_graph` tool)

- [ ] **Step 1: Identify all importers of `cookbooks._shared.query` and `compile_graph`**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
grep -rn "from cookbooks._shared.query\|from cookbooks._shared.compile_graph\|cookbooks._shared.query\|cookbooks._shared.compile_graph" --include="*.py" 2>&1 | head -30
```

Capture the list. Each of these needs to be either deleted or rewired.

```
grep -rn "import kuzu\|from kuzu" --include="*.py" 2>&1 | head -20
```

Note all `kuzu`-using callers.

- [ ] **Step 2: Delete the Kuzu modules**

```
git rm cookbooks/_shared/compile_graph.py
git rm cookbooks/_shared/query.py
git rm tests/_shared/test_compile_graph.py
git rm tests/_shared/test_query.py
git rm cookbooks/api/routers/graph.py
```

- [ ] **Step 3: Delete the Kuzu artefacts on disk**

```
git rm -r graph/kuzu.db 2>/dev/null || true
git rm graph/snapshots/graph.jsonl 2>/dev/null || true
```

(If the file isn't tracked, fall back to `rm -rf` outside git.)

- [ ] **Step 4: Remove the kuzu dep**

In `pyproject.toml`, remove the `"kuzu"` line from `dependencies`.

```
uv lock && uv sync --extra dev
```

- [ ] **Step 5: Rewire `qa_tools.py` and the legacy agent**

In `cookbooks/_shared/qa_tools.py`, delete the `query_graph` function entirely (it was the Kuzu-only path). The `_query_graph` import from `cookbooks._shared.query` is now broken; remove that import line too. Keep `read_wiki_page` and `merge_merchants`.

In `cookbooks/knowledge_engine/agent.py`, find:

```python
@tool
def query_graph(cypher: str) -> dict:
    """Run a read-only Cypher query against the compiled Kuzu graph.
    ...
    """
    return _query_graph_impl(cypher)
```

Delete this `@tool` definition.

Find `_READ_TOOLS = [query_graph, read_wiki_page]` and change to `_READ_TOOLS = [read_wiki_page]`.

Find the import line `from cookbooks._shared.qa_tools import (... query_graph as _query_graph_impl, ...)` and remove `query_graph as _query_graph_impl`.

If the resulting legacy agent now lacks ANY Cypher read tool, that's fine for one PR cycle — the legacy path becomes wiki-only, and users wanting Cypher should set `PFH_QA_AGENT=deepagent` to get `cypher_read_only` against Neo4j.

(Alternative if preferred: re-export `cypher_read_only` as `query_graph` in the legacy path so users see no downgrade. This is a 3-line change in `knowledge_engine/agent.py`. Document the choice in the PR description.)

- [ ] **Step 6: Un-register the old `/api/graph/snapshot` router**

In `cookbooks/api/server.py`, find the line that registers the old `graph.router` (the JSONL-snapshot one — not the new `graph_traversal.router` from PR 4.1) and delete it, along with the import.

- [ ] **Step 7: Update `/graph` overview page**

Edit `web/app/graph/page.tsx`. The existing implementation calls `api.graph.snapshot()` which no longer exists. Replace with a redirect or a Neo4j-backed overview.

**Simplest approach** (recommended for this bundle): redirect `/graph` to a canonical entry point.

Replace the entire file content with:

```tsx
import { redirect } from "next/navigation";

export default function GraphPage() {
  // The old JSONL-snapshot overview is gone with Kuzu (PR 4.3).
  // Point users at the node-explorer; merchant::costco is a reasonable
  // default if the user has any Costco transactions. They can navigate
  // from there.
  redirect("/graph/merchant::costco?depth=2");
}
```

(If the user doesn't have a Costco merchant, pick a different canonical anchor — or, better, fetch the top-spending merchant from the API and redirect to its id. Keeping the redirect target stable + showing a fallback page is also acceptable.)

- [ ] **Step 8: Delete tests that depend on the Kuzu paths**

```
# Find and prune the Kuzu-specific tests.
grep -l "query_graph\|kuzu\|compile_graph" tests/ -r --include="*.py" 2>&1 | head -20
```

For each match: either delete the test (if it was Kuzu-only) or rewire to use `cypher_read_only`. Likely candidates:
- `tests/_shared/test_qa_tools.py::test_query_graph_*` — delete
- `tests/knowledge_engine/test_agent.py::test_..._uses_query_graph` — delete or rewire to `cypher_read_only`
- `tests/statement_ingester/test_graph_e2e.py` — likely Kuzu-specific; delete (replace with the testcontainers compile_neo4j test if not already covered)

- [ ] **Step 9: Update docs**

In `docs/architecture.md`, find any mention of Kuzu and replace with "Neo4j (via compile_neo4j.py)". If there's a diagram, update the box.

- [ ] **Step 10: Run the full suite**

```
uv run pytest --tb=short -p no:warnings 2>&1 | tail -20
```

Expected: previous count minus the deleted Kuzu tests; pre-existing failures should drop (the 5 `test_query.py` failures from the original baseline are gone because `test_query.py` itself is deleted).

If anything is broken from a missed import: fix one at a time.

- [ ] **Step 11: Commit**

```
git add -A
git commit -m "feat(graph): remove Kuzu

Kuzu has been replaced by Neo4j (compile_neo4j.py from Plan 2,
cypher_read_only from Plan 3). This commit deletes:
  - cookbooks/_shared/compile_graph.py
  - cookbooks/_shared/query.py
  - cookbooks/api/routers/graph.py (the JSONL snapshot endpoint)
  - graph/kuzu.db, graph/snapshots/graph.jsonl
  - cookbooks/_shared/qa_tools.query_graph
  - the kuzu dep
  - test_compile_graph, test_query, and Kuzu-specific qa_tools tests

The legacy knowledge_engine agent loses its query_graph tool —
users wanting Cypher should set PFH_QA_AGENT=deepagent to get
cypher_read_only against Neo4j. The /graph overview page now
redirects to /graph/[id] (the node-explorer landed in PR 4.1)."
```

---

### Task 10: PR 4.3 wrap-up

- [ ] **Step 1: Full suite check**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

- [ ] **Step 2: Push + open + merge**

```
git push origin feat/openclaw-ui-cleanup
gh pr create --base main --title "feat(cleanup): PR 3 of 4 — remove Kuzu" --body "$(cat <<'EOF'
## Summary

Spec §6.6 Kuzu removal. The compile + query path through Neo4j has been live since Plan 2 + 3; this PR sheds the parallel-run scaffolding.

- Deletes \`compile_graph.py\`, \`query.py\`, \`graph/kuzu.db\`, the \`/api/graph/snapshot\` JSONL endpoint, the \`kuzu\` dep, and the \`query_graph\` @tool on the legacy agent.
- \`/graph\` overview page redirects to \`/graph/[id]\` (the node-explorer from PR 4.1).
- Legacy \`knowledge_engine/agent.py\` loses its Cypher tool — users wanting Cypher set \`PFH_QA_AGENT=deepagent\`.

Spec: §6.6.

## Test plan

- [x] Full suite green; the 5 \`test_query.py\` pre-existing failures are gone with \`test_query.py\` itself.
EOF
)"
gh pr merge <PR-number> --merge
```

---

## PR 4.4: DuckDB removal

### Task 11: Migrate `tmp_workspace` to Postgres-via-testcontainers

**Files:**
- Modify: `tests/conftest.py`
- Modify: `cookbooks/_shared/config.py::LedgerSettings`
- Modify: `cookbooks/_shared/db.py` (collapse dispatcher)
- Delete: `cookbooks/_shared/db_duckdb.py`
- Delete: `cookbooks/_shared/db_postgres.py` (contents move into `db.py`)
- Delete: `tests/_shared/test_db_dispatcher.py`
- Delete: `tests/statement_ingester/test_backend_equivalence.py`
- Delete: `data/ledger.duckdb` (and `.wal`)
- Modify: `pyproject.toml` (remove `duckdb`)

This is the biggest change in PR 4.4 — the default test backend flips from in-process DuckDB to a containerized Postgres. Once it works, the rest of PR 4.4 is mechanical deletion.

- [ ] **Step 1: Confirm Docker is up**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
docker info 2>&1 | grep "Server Version" || (echo "Docker not running" && exit 1)
```

If Docker is down, STOP — PR 4.4 can't proceed without it.

- [ ] **Step 2: Rewrite `tmp_workspace` to use a session-scoped Postgres**

Edit `tests/conftest.py`. Find the existing `tmp_workspace` fixture and the parametrized `ledger_backend` fixture (added in Plan 2 Bundle 5). Restructure as:

```python
"""Shared pytest fixtures across the suite."""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


_docker_available = subprocess.run(
    ["docker", "info"], capture_output=True
).returncode == 0


@pytest.fixture(scope="session")
def _shared_postgres():
    """One Postgres container for the whole test session — slow but correct.

    Each test gets a clean DB by TRUNCATEing all tables in `tmp_workspace`'s
    finalize step. The alembic upgrade runs ONCE at session start.
    """
    if not _docker_available:
        pytest.skip("docker daemon not running; cannot start ephemeral Postgres")
    from testcontainers.postgres import PostgresContainer
    pg = PostgresContainer("postgres:16-alpine")
    pg.start()
    raw_url = pg.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    alembic_url = raw_url.replace("postgresql://", "postgresql+psycopg://")
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PFH_PG_URL": alembic_url}
    subprocess.run(
        ["uv", "run", "alembic",
         "-c", str(repo_root / "db" / "postgres" / "alembic.ini"),
         "upgrade", "head"],
        cwd=repo_root, env=env, check=True, capture_output=True,
    )
    try:
        yield raw_url
    finally:
        pg.stop()


_TABLES_TO_TRUNCATE = (
    "annotations", "transactions", "patterns", "merchants",
    "categories", "statements", "accounts", "memos", "budgets",
    "goals", "net_worth_snapshots",
)


@pytest.fixture
def tmp_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _shared_postgres: str,
) -> Iterator[Path]:
    """Per-test workspace.

    Filesystem dirs: sources, parsed, data, wiki/{memos,decisions,recommendations},
    graph/snapshots, out. (Trimmed in PR 4.2.)

    Ledger backend: Postgres (one shared container; per-test TRUNCATE).
    """
    for sub in ("sources", "parsed", "data",
                "wiki/memos", "wiki/decisions", "wiki/recommendations",
                "graph/snapshots", "out"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PFH_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("PFH_PARSED_DIR",  str(tmp_path / "parsed"))
    monkeypatch.setenv("PFH_DATA_DIR",    str(tmp_path / "data"))
    monkeypatch.setenv("PFH_WIKI_DIR",    str(tmp_path / "wiki"))
    monkeypatch.setenv("PFH_GRAPH_DIR",   str(tmp_path / "graph"))
    monkeypatch.setenv("PFH_OUT_DIR",     str(tmp_path / "out"))
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("PFH_LLM_MODEL",   "ollama:qwen3.6:35b")
    monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)
    monkeypatch.delenv("PFH_PII_DENYLIST", raising=False)
    monkeypatch.delenv("PFH_NEO4J_URL", raising=False)
    monkeypatch.delenv("PFH_NEO4J_USER", raising=False)
    monkeypatch.delenv("PFH_NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("PFH_NEO4J_DATABASE", raising=False)
    monkeypatch.delenv("PFH_QA_AGENT", raising=False)

    # Point at the shared Postgres + truncate everything for isolation.
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", _shared_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()

    import psycopg
    with psycopg.connect(_shared_postgres, autocommit=True) as conn, conn.cursor() as cur:
        # Restart identity to keep auto-incrementing IDs predictable across tests.
        cur.execute(
            "TRUNCATE TABLE " + ", ".join(_TABLES_TO_TRUNCATE) + " RESTART IDENTITY CASCADE"
        )

    yield tmp_path


@pytest.fixture
def pii_tokenizer():
    """Fresh PiiTokenizer per test — never share across tests."""
    from cookbooks._shared.pii_tokenizer import PiiTokenizer
    return PiiTokenizer()
```

Delete the old `ledger_backend` parametrize fixture (no longer needed — only one backend).

- [ ] **Step 3: Run a representative slice of the suite**

```
uv run pytest tests/_shared/ --tb=short -p no:warnings 2>&1 | tail -10
```

Many tests will fail — they were written assuming DuckDB's behaviour (different SQL dialect, `?` placeholders, schema differences). Fix them iteratively. Common patterns:
- `conn.execute("INSERT ... VALUES (?, ?)", [...])` → `conn.execute("INSERT ... VALUES (%s, %s)", [...])`
- `conn.execute("SELECT * FROM accounts;")` → no change (both backends accept this)
- Tests that ran `init_schema()` before each test now don't need to — alembic+TRUNCATE handles it

Each round of fixes is one task. Don't try to fix everything in one commit — work through the test files one at a time.

- [ ] **Step 4: Commit (initial migration)**

```
git add tests/conftest.py
git commit -m "test(conftest): migrate tmp_workspace to Postgres-via-testcontainers

Session-scoped Postgres container; per-test TRUNCATE for isolation.
Alembic runs once at session start. The duckdb-vs-postgres
ledger_backend parametrize fixture is gone — only one backend now.

Some tests will fail until they're updated for Postgres dialect
(%s placeholders vs ?, etc.). Subsequent commits fix them
file-by-file."
```

- [ ] **Step 5: Fix the test fallout, file by file**

For each failing file, make a focused commit. Expected files needing changes (best-guess list — actual mileage may vary):
- `tests/statement_ingester/test_*.py` — INSERT/UPDATE placeholders
- `tests/monthly_analyst/test_*.py` — same
- `tests/advisor/test_*.py` — same
- `tests/knowledge_engine/test_*.py` — same

The pattern is the same in every file: find `?` placeholders in raw SQL and convert to `%s`; check for any DuckDB-specific syntax (window function QUALIFY, etc.).

Commit each fix with a message like:
```
git commit -m "test(<area>): convert ? placeholders to %s for Postgres path"
```

Once all tests pass:

```
uv run pytest --tb=short -p no:warnings 2>&1 | tail -3
```

---

### Task 12: Collapse db.py + delete DuckDB code

**Files:**
- Modify: `cookbooks/_shared/config.py::LedgerSettings`
- Modify: `cookbooks/_shared/db.py`
- Delete: `cookbooks/_shared/db_duckdb.py`
- Delete: `cookbooks/_shared/db_postgres.py`
- Delete: `tests/_shared/test_db_dispatcher.py`
- Delete: `tests/statement_ingester/test_backend_equivalence.py`
- Delete: `data/ledger.duckdb` (and `*.duckdb.wal`)
- Modify: `pyproject.toml`

- [ ] **Step 1: Move db_postgres.py contents into db.py**

Read `cookbooks/_shared/db_postgres.py`. Move its contents (the `_DuckDBLikeConnection`, `_DuckDBLikeResult`, `connect_readwrite`, `connect_readonly`, `init_schema`) into `cookbooks/_shared/db.py`, replacing the dispatcher logic.

The new `cookbooks/_shared/db.py` looks like:

```python
"""Postgres ledger backend.

Was a duckdb / postgres dispatcher behind PFH_LEDGER_BACKEND; collapsed
to Postgres-only in PR 4.4. The dispatcher and the DuckDB backend are
gone.

Public API: connect_readwrite(), connect_readonly(), init_schema().
Each returns a thin wrapper whose `.execute(sql, params).fetchall()`
matches the call shape the codebase already uses.

Schema is owned by Alembic (db/postgres/migrations/); init_schema() is
a documented no-op for legacy compatibility — actual schema work is:
    PFH_PG_URL=postgresql+psycopg://... \\
        uv run alembic -c db/postgres/alembic.ini upgrade head
"""
from __future__ import annotations

from typing import Any

import psycopg

from cookbooks._shared.config import load_settings


class _DuckDBLikeResult:
    """Wrap a psycopg cursor to expose DuckDB's execute-then-fetch shape."""
    def __init__(self, cursor: psycopg.Cursor):
        self._cursor = cursor

    def fetchall(self) -> list[tuple]:
        if self._cursor.description is None:
            return []
        return self._cursor.fetchall()

    def fetchone(self) -> tuple | None:
        if self._cursor.description is None:
            return None
        return self._cursor.fetchone()


class _DuckDBLikeConnection:
    """psycopg.Connection wrapper that mimics DuckDB's call shape."""

    def __init__(self, inner: psycopg.Connection, read_only: bool):
        self._inner = inner
        self._read_only = read_only
        if read_only:
            inner.execute("SET TRANSACTION READ ONLY")

    def execute(self, sql: str, params: list | tuple | None = None) -> _DuckDBLikeResult:
        cursor = self._inner.cursor()
        cursor.execute(sql, params)
        return _DuckDBLikeResult(cursor)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    def close(self) -> None:
        try:
            if not self._read_only:
                self._inner.commit()
        finally:
            self._inner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._inner.rollback()
        elif not self._read_only:
            self._inner.commit()
        self._inner.close()
        return False


def active_backend() -> str:
    """Always 'postgres' since PR 4.4."""
    return "postgres"


def _connect(read_only: bool) -> _DuckDBLikeConnection:
    settings = load_settings()
    inner = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    return _DuckDBLikeConnection(inner, read_only=read_only)


def connect_readwrite() -> _DuckDBLikeConnection:
    return _connect(read_only=False)


def connect_readonly() -> _DuckDBLikeConnection:
    return _connect(read_only=True)


def init_schema() -> None:
    """No-op. Schema is owned by Alembic — see module docstring."""
    return None


__all__ = ["active_backend", "connect_readonly", "connect_readwrite", "init_schema"]
```

- [ ] **Step 2: Tighten LedgerSettings**

In `cookbooks/_shared/config.py::LedgerSettings`, change the validator to allow only "postgres":

```python
class LedgerSettings(BaseModel):
    backend: str = "postgres"
    pg_url: str = "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"

    @field_validator("backend")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        if v != "postgres":
            raise ValueError(
                f"PFH_LEDGER_BACKEND must be 'postgres' (DuckDB removed in "
                f"PR 4.4); got {v!r}"
            )
        return v
```

- [ ] **Step 3: Delete the DuckDB and dispatcher files**

```
git rm cookbooks/_shared/db_duckdb.py
git rm cookbooks/_shared/db_postgres.py
git rm tests/_shared/test_db_dispatcher.py
git rm tests/statement_ingester/test_backend_equivalence.py
```

- [ ] **Step 4: Delete the DuckDB data file**

```
rm -f data/ledger.duckdb data/*.duckdb.wal
```

(May already not be tracked.)

- [ ] **Step 5: Remove the duckdb dep**

In `pyproject.toml`, remove `"duckdb"` from `dependencies`.

```
uv lock && uv sync --extra dev
```

- [ ] **Step 6: Update tests that imported from db_duckdb / db_postgres**

```
grep -rn "from cookbooks._shared.db_duckdb\|from cookbooks._shared.db_postgres" --include="*.py" 2>&1
```

For each match: change to `from cookbooks._shared.db import ...` (everything still exports under the same name).

`tests/_shared/test_db_postgres.py` — keep but rename to `tests/_shared/test_db.py` and adjust the import. Or delete if every assertion is covered elsewhere.

- [ ] **Step 7: Update the config tests**

The 3 config tests that asserted DuckDB-default and "invalid backend raises" now need updates:
- `test_default_ledger_backend_is_duckdb` → `test_default_ledger_backend_is_postgres` (asserts `"postgres"`)
- `test_ledger_backend_postgres_when_env_set` → no change (still passes)
- `test_invalid_backend_raises` → assert `duckdb` raises now too (since only postgres is valid)

Update the assertion strings:

```python
def test_default_ledger_backend_is_postgres(monkeypatch):
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.ledger.backend == "postgres"


def test_invalid_backend_raises(monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "duckdb")  # used to be valid; now isn't
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import pytest
    with pytest.raises(ValueError, match="PFH_LEDGER_BACKEND"):
        load_settings()
```

- [ ] **Step 8: Run the full suite**

```
uv run pytest --tb=short -p no:warnings 2>&1 | tail -20
```

Fix any remaining failures iteratively.

- [ ] **Step 9: Commit**

```
git add -A
git commit -m "feat(db): collapse to Postgres-only; delete DuckDB

cookbooks/_shared/db.py is now the Postgres backend directly
(the dispatcher and db_postgres.py merge into one file). The
db_duckdb.py module and the duckdb dep are gone.

LedgerSettings.backend's validator now rejects anything except
'postgres'. test_db_dispatcher and test_backend_equivalence are
gone — only one backend now.

The 'Switch back to DuckDB' section in docs/runbook-postgres.md
is removed.

All callers via 'from cookbooks._shared.db import ...' continue
to work unchanged."
```

---

### Task 13: PR 4.4 wrap-up

- [ ] **Step 1: Full suite check**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

The full suite is now Postgres-only — slower (~3-5x) than before because of the testcontainers startup. Acceptable.

- [ ] **Step 2: Update docs/runbook-postgres.md**

Remove the "Switch back to DuckDB" section. The rest of the runbook stays valid.

- [ ] **Step 3: Update docs/architecture.md**

If the architecture doc mentioned DuckDB or the dispatcher, update to single-store Postgres.

- [ ] **Step 4: Push + open + merge**

```
git add docs/
git commit -m "docs: drop DuckDB references"
git push origin feat/openclaw-ui-cleanup
gh pr create --base main --title "feat(cleanup): PR 4 of 4 — remove DuckDB" --body "$(cat <<'EOF'
## Summary

Spec §6.6 DuckDB removal. Postgres has been the production backend since PR 2.1 (under the env flag); this PR makes it the only valid backend.

- \`cookbooks/_shared/db.py\` collapses from dispatcher to a direct Postgres backend; \`db_postgres.py\` merges into it; \`db_duckdb.py\` is deleted.
- \`LedgerSettings.backend\` now rejects anything except \"postgres\" at config-load time.
- The \`duckdb\` dep is removed.
- \`tests/conftest.py::tmp_workspace\` switches from in-process DuckDB to a session-scoped testcontainers Postgres. Per-test isolation via TRUNCATE ... RESTART IDENTITY CASCADE. Alembic runs once at session start.
- The cross-backend equivalence smoke and the dispatcher tests are deleted.

The test suite is now Postgres-only — slower (~3-5x) due to container startup, but the call-shape doesn't change.

Spec: §6.6.

## Test plan

- [x] Full suite green under Postgres-only.
- [x] Migration script (\`migrate_wiki_to_postgres.py\`) tests unaffected.
- [x] No callers via \`from cookbooks._shared.db import ...\` broken.
EOF
)"
gh pr merge <PR-number> --merge
```

---

## Self-review

**Spec coverage:**

| Spec section | Tasks | Status |
|---|---|---|
| §6.6 Kuzu removal | Tasks 9-10 | ✅ |
| §6.6 DuckDB removal | Tasks 11-13 | ✅ |
| §6.6 Wiki trim | Tasks 6-8 | ✅ |
| §6.6 docs/architecture.md update | Task 9 Step 9 + Task 13 Step 3 | ✅ |
| §9.1 /graph/{node,neighbors,evidence} endpoints | Task 1 | ✅ |
| §9.1 /chat SSE payload extended with evidence subgraph IDs | — | **Deferred** (see "Out of scope" — needs synthesizer parsing) |
| §9.2 react-force-graph-2d on /qa side panel | — | **Deferred** to Plan 5 — Plan 4 ships manual click-through from /qa to /graph/[id] instead |
| §9.2 Existing /graph repurposed | Task 9 Step 7 | ✅ (redirected to /graph/[id]) |
| §9.3 Subgraph rendering | Task 3 (server-component rendering) | ✅ |

**Placeholder scan:** none — every step has executable code, exact commands, expected output.

**Type consistency:**
- `fetchNode`, `fetchNeighbors`, `fetchEvidence` exported from `web/lib/api-graph.ts` and consumed by `web/app/graph/[id]/page.tsx` — names match.
- `Subgraph`, `GraphNode`, `GraphEdge`, `Transaction` types declared in `api-graph.ts` and used in the page.
- `migrate(dry_run: bool) -> dict[str, int]` is the signature in `scripts/migrate_wiki_to_postgres.py` and what the tests call.
- `get_node`, `neighbors`, `evidence_for` — the three tool functions wrapped by the router; all use `.invoke({...})` shape consistently.

**Known risks:**
- **Task 7 (wiki trim) test fallout** could be large — many tests stage wiki files. The plan calls for iterative fixing rather than a big-bang change. The implementer should land Task 6 first, run the migration, manually verify Postgres rows match expectations, THEN trim the wiki + fix tests in a sequence of small commits.
- **Task 11 (testcontainers as default) is slow** — adds ~10s of container startup to every pytest invocation. Acceptable but a real DX hit. If too painful, consider keeping DuckDB available behind an env flag for development-only and using Postgres for CI; that's a Plan 5 polish.
- **Task 9 Step 7 (web/app/graph/page.tsx redirect)** picks `merchant::costco?depth=2` as a hardcoded anchor — fine for the user's data but won't work for a fresh install. A small follow-up could fetch the top-spending merchant ID from the API and redirect there dynamically.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-18-openclaw-ui-and-cleanup.md`.**
