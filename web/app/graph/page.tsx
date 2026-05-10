import { GraphView } from "@/components/GraphView";
import { api } from "@/lib/api";

export default async function GraphPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const sp = await searchParams;
  const snap = await api.graph.snapshot({ type: sp.type, limit: 800 });
  const counts: Record<string, number> = {};
  for (const n of snap.nodes) counts[n.type] = (counts[n.type] ?? 0) + 1;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Graph</h1>
        <p className="text-sm opacity-70">
          Interactive force-directed view from{" "}
          <code>graph/snapshots/graph.jsonl</code>. Click a node to open
          its wiki page. <strong>{snap.node_count}</strong> nodes ·{" "}
          <strong>{snap.edge_count}</strong> edges
          {sp.type ? <> · filtered to <code>type={sp.type}</code></> : null}.
        </p>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <a href="/graph"
           className={`border ${!sp.type ? "border-info" : "border-black/10 dark:border-white/10"} rounded p-2 text-center hover:border-info`}>
          <div className="text-xs uppercase opacity-60">all</div>
          <div className="text-base font-semibold">{snap.node_count}</div>
        </a>
        {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([t, n]) => (
          <a key={t} href={`/graph?type=${t}`}
             className={`border ${sp.type === t ? "border-info" : "border-black/10 dark:border-white/10"} rounded p-2 text-center hover:border-info`}>
            <div className="text-xs uppercase opacity-60">{t}</div>
            <div className="text-base font-semibold">{n}</div>
          </a>
        ))}
      </section>

      <GraphView snapshot={snap} />

      <p className="text-xs opacity-60">
        For a more polished offline view, run{" "}
        <code className="font-mono">python -m cookbooks.statement_ingester graph-stats</code>
        {" "}and open <code className="font-mono">graph/visualization/graph.html</code>.
      </p>
    </div>
  );
}
