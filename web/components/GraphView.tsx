"use client";

import dynamic from "next/dynamic";
import { useMemo, useRef } from "react";

import type { GraphSnapshot } from "@/lib/api";
import { pageRoute } from "@/lib/wikilinks";

// react-force-graph-2d touches `window` on import — load client-only.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => <p className="opacity-60">loading graph…</p>,
});

const COLOURS: Record<string, string> = {
  Account:      "#4c9aff",
  Statement:    "#36b37e",
  Merchant:     "#ffab00",
  Category:     "#ff5630",
  Subscription: "#6554c0",
  Transaction:  "#97a0af",
  Memo:         "#0a8754",
  Recommendation: "#cc4499",
  ConceptReview:  "#cc8844",
  Decision:     "#888888",
  Budget:       "#22aaee",
  Annotation:   "#aaaaaa",
};

type Node = { id: string; label: string; type: string; color: string };
type Edge = { source: string; target: string; type: string };

export function GraphView({ snapshot }: { snapshot: GraphSnapshot }) {
  const ref = useRef<HTMLDivElement>(null);

  const data = useMemo(() => {
    const nodes: Node[] = snapshot.nodes.map((n) => ({
      id: String(n.id),
      label: String((n as Record<string, unknown>).name ?? (n as Record<string, unknown>).canonical_name ?? n.id),
      type: n.type,
      color: COLOURS[n.type] ?? "#888",
    }));
    const ids = new Set(nodes.map((n) => n.id));
    const links: Edge[] = snapshot.edges
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((e) => ({ source: e.from, target: e.to, type: e.type }));
    return { nodes, links };
  }, [snapshot]);

  return (
    <div ref={ref} className="border border-black/10 dark:border-white/10 rounded overflow-hidden bg-paper dark:bg-ink"
         style={{ height: 600 }}>
      <ForceGraph2D
        graphData={data}
        nodeRelSize={4}
        linkColor={() => "rgba(120,120,120,0.35)"}
        nodeAutoColorBy="type"
        nodeCanvasObject={(node, ctx, globalScale) => {
          const n = node as unknown as Node;
          ctx.fillStyle = n.color;
          ctx.beginPath();
          ctx.arc((node as { x: number }).x, (node as { y: number }).y, 4, 0, 2 * Math.PI);
          ctx.fill();
          if (globalScale > 2.5) {
            ctx.fillStyle = "currentColor";
            ctx.font = `${10 / globalScale}px sans-serif`;
            ctx.fillText(n.label.slice(0, 16),
              (node as { x: number }).x + 6, (node as { y: number }).y + 3);
          }
        }}
        onNodeClick={(node) => {
          const route = pageRoute(String(node.id));
          window.location.href = route;
        }}
      />
    </div>
  );
}
