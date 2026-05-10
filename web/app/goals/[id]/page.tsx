import { notFound } from "next/navigation";

import { api } from "@/lib/api";
import { Wikilink } from "@/lib/wikilinks";

export default async function GoalDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ period?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  let g;
  try { g = await api.goals.get(id); } catch { notFound(); }
  if (!g) notFound();

  const periodForProgress = sp.period;
  let progress = null;
  if (periodForProgress) {
    try {
      progress = await fetch(
        `${typeof window === "undefined" ? "http://127.0.0.1:8000" : ""}/api/goals/${id}/progress?period=${periodForProgress.replace("-", "_")}`,
        { method: "POST", cache: "no-store" },
      ).then((r) => r.ok ? r.json() : null);
    } catch { /* swallow */ }
  }

  const scopeLink = g.scope_type === "savings_account" || g.scope_type === "debt_payoff"
    ? `acct_${g.scope_id}`
    : g.scope_type === "category_underspend"
      ? `cat_${g.scope_id}`
      : g.scope_id;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-mono opacity-60">{g.id}</p>
        <h1 className="text-2xl font-semibold">{g.name}</h1>
        <div className="mt-2 flex flex-wrap gap-2 items-center text-sm">
          <span className={`badge ${
            g.status === "active"   ? "badge-info" :
            g.status === "paused"   ? "badge-warn" :
            g.status === "achieved" ? "badge-positive" :
            g.status === "missed"   ? "badge-negative" : "badge-info"
          }`}>{g.status}</span>
          <span>target £{g.target_amount} by <code className="text-xs">{g.target_date}</code></span>
          <span>· scope <Wikilink id={scopeLink} /></span>
          {g.started_at && <span>· started {g.started_at}</span>}
        </div>
        {g.notes && <p className="text-sm opacity-80 mt-2">{g.notes}</p>}
      </header>

      <section className="border border-black/10 dark:border-white/10 rounded p-4">
        <form action="" method="GET" className="flex gap-2 mb-3">
          <label className="text-sm self-center opacity-70">progress as of</label>
          <input name="period" defaultValue={periodForProgress ?? ""}
                 placeholder="yyyy_mm" className="border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm" />
          <button type="submit" className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5">
            show
          </button>
        </form>

        {progress ? (
          <ul className="space-y-1 text-sm font-mono">
            <li>current:           £{progress.current_amount}</li>
            <li>pct_complete:      {(progress.pct_complete * 100).toFixed(1)}%</li>
            <li>months:            {progress.months_elapsed} / {progress.months_total}</li>
            <li>monthly_required:  £{progress.monthly_required}</li>
            <li>status:            <strong>{progress.status}</strong></li>
            <li>on_track:          {progress.on_track ? <span className="badge badge-positive">yes</span> : <span className="badge badge-warn">NO</span>}</li>
          </ul>
        ) : (
          <p className="opacity-60 text-sm">Enter a period above to score this goal.</p>
        )}
      </section>
    </div>
  );
}
