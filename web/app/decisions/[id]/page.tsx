import { notFound } from "next/navigation";

import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api";

export default async function DecisionDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail;
  try { detail = await api.decisions.get(id); } catch { notFound(); }
  if (!detail) notFound();
  const r = detail.replay;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-mono opacity-60">{detail.page.id}</p>
        <h1 className="text-2xl font-semibold">Decision · {r.action_id}</h1>
        <p className="text-sm opacity-70">by <code>{r.actor}</code> at <code>{r.ts.slice(0, 19)}Z</code></p>
      </header>
      <section className="border border-black/10 dark:border-white/10 rounded p-3">
        <h2 className="text-sm font-semibold uppercase opacity-60 tracking-wide mb-2">Replay</h2>
        <ul className="text-sm space-y-1 font-mono">
          <li>live pages at ts: <strong>{r.live_pages_at_ts}</strong></li>
          <li>prior decisions:  <strong>{r.prior_decisions_count}</strong></li>
          <li>wiki fingerprint drift: {r.wiki_fingerprint_drift
            ? <span className="badge badge-negative">YES</span>
            : <span className="badge badge-positive">no</span>}</li>
          <li>ontology fingerprint drift: {r.ontology_fingerprint_drift
            ? <span className="badge badge-negative">YES</span>
            : <span className="badge badge-positive">no</span>}</li>
        </ul>
      </section>
      <article>
        <MarkdownView body={detail.page.body} />
      </article>
    </div>
  );
}
