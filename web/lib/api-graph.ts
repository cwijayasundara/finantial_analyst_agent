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
