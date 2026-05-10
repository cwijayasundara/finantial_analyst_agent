import Link from "next/link";

import { api, type GoalProgress, type GoalRow } from "@/lib/api";

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

export default async function GoalsPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string; status?: string }>;
}) {
  const sp = await searchParams;
  const status = sp.status || "active";
  const period = sp.period;

  const [goals, progress] = await Promise.all([
    safe<GoalRow[]>(api.goals.list(status === "all" ? undefined : status), []),
    period ? safe<GoalProgress[]>(api.goals.progress(period), []) : Promise.resolve([] as GoalProgress[]),
  ]);

  const progressById = new Map(progress.map((p) => [p.goal_id, p]));

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Goals</h1>
        <p className="text-sm opacity-70">
          Plan-mode targets. Each goal is scored against a period; the
          advisor emits a <code>goal_off_track</code> recommendation when
          you fall meaningfully behind. Add via{" "}
          <code className="text-xs">python -m cookbooks.statement_ingester goal set</code>.
        </p>
      </header>

      <form action="" method="GET" className="flex flex-wrap gap-2 items-end">
        <div>
          <label className="block text-xs uppercase opacity-60 mb-1">status</label>
          <select name="status" defaultValue={status}
                  className="border border-black/10 dark:border-white/10 rounded px-2 py-1.5 bg-transparent text-sm">
            <option value="active">active</option>
            <option value="paused">paused</option>
            <option value="achieved">achieved</option>
            <option value="missed">missed</option>
            <option value="all">all</option>
          </select>
        </div>
        <div>
          <label className="block text-xs uppercase opacity-60 mb-1">score as-of period</label>
          <input name="period" defaultValue={period ?? ""} placeholder="yyyy_mm"
                 className="border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm" />
        </div>
        <button type="submit" className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5">
          apply
        </button>
      </form>

      {goals.length === 0 ? (
        <p className="opacity-70">No goals with status <code>{status}</code>.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left opacity-60 text-xs uppercase tracking-wide">
            <tr>
              <th className="py-2">name</th>
              <th>scope</th>
              <th className="text-right">target</th>
              <th>by</th>
              {period && <>
                <th className="text-right">progress</th>
                <th>status</th>
              </>}
            </tr>
          </thead>
          <tbody className="divide-y divide-black/10 dark:divide-white/10">
            {goals.map((g) => {
              const p = progressById.get(g.id);
              return (
                <tr key={g.id} className="hover:bg-black/5 dark:hover:bg-white/5">
                  <td className="py-2">
                    <Link href={`/goals/${g.id}`} className="font-medium hover:underline">
                      {g.name}
                    </Link>
                  </td>
                  <td>{g.scope_type}/<code className="text-xs">{g.scope_id}</code></td>
                  <td className="text-right">£{g.target_amount}</td>
                  <td className="font-mono text-xs">{g.target_date}</td>
                  {period && (
                    <>
                      <td className="text-right font-mono">
                        {p ? `£${p.current_amount} (${(p.pct_complete * 100).toFixed(0)}%)` : "—"}
                      </td>
                      <td>
                        {p ? (
                          <span className={`badge ${
                            p.status === "on_track" ? "badge-positive" :
                            p.status === "ahead"    ? "badge-positive" :
                            p.status === "behind"   ? "badge-warn" :
                            p.status === "missed"   ? "badge-negative" : "badge-info"
                          }`}>{p.status}</span>
                        ) : (
                          <span className="badge badge-info">{g.status}</span>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
