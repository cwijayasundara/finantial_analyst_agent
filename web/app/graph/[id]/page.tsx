// Server component — fetches the subgraph for one node id, hands it
// to the existing client GraphView component for force-directed rendering.

import Link from "next/link";

import { GraphView } from "@/components/GraphView";
import { fetchEvidence, fetchNeighbors, fetchNode } from "@/lib/api-graph";

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

  // GraphView expects the JSONL snapshot shape — adapt the new
  // {id, label} / {source, target, type} shape into it.
  const snap = {
    nodes: subgraph.nodes.map(n => ({
      kind: "node" as const,
      id: n.id,
      type: n.label,
      ...(n.properties ?? {}),
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
          <Link
            href={`/graph/${encodeURIComponent(id)}?depth=${Math.max(1, depth - 1)}`}
            className="underline"
          >
            depth −1
          </Link>
          {" · "}
          <Link
            href={`/graph/${encodeURIComponent(id)}?depth=${Math.min(4, depth + 1)}`}
            className="underline"
          >
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
