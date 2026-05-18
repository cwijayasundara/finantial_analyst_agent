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
