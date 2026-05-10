import Link from "next/link";

import { api } from "@/lib/api";

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

export default async function NetWorthPage() {
  const rows = await safe(api.networth.list(), []);

  // Compute month-over-month deltas client-side from the chronological list
  const withDelta = rows.map((r, i) => {
    const prev = rows[i - 1];
    const delta = prev ? Number(r.total_amount) - Number(prev.total_amount) : null;
    return { ...r, delta };
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Net Worth</h1>
        <p className="text-sm opacity-70">
          Multi-account total position over time. Snapshots are persisted
          by the analyst on each monthly run, or via{" "}
          <code className="text-xs">python -m cookbooks.statement_ingester networth snapshot &lt;yyyy_mm&gt;</code>.
        </p>
      </header>

      {rows.length === 0 ? (
        <p className="opacity-70">
          No snapshots yet. Run{" "}
          <code className="text-xs">python -m cookbooks.monthly_analyst analyse 2025_04</code>
          {" "}or take a manual snapshot.
        </p>
      ) : (
        <>
          <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card title="Latest period" value={rows[rows.length - 1].period} />
            <Card title="Latest total"  value={`£${rows[rows.length - 1].total_amount}`} />
            <Card title="Snapshots"     value={rows.length} />
          </section>

          <table className="w-full text-sm">
            <thead className="text-left opacity-60 text-xs uppercase tracking-wide">
              <tr>
                <th className="py-2">period</th>
                <th className="text-right">total</th>
                <th className="text-right">Δ MoM</th>
                <th>by account</th>
                <th>computed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/10 dark:divide-white/10">
              {withDelta.slice().reverse().map((r) => (
                <tr key={r.period}>
                  <td className="py-2 font-mono">
                    <Link href={`/wiki/snap_${r.period}`} className="hover:underline">
                      {r.period}
                    </Link>
                  </td>
                  <td className="text-right font-semibold">£{r.total_amount}</td>
                  <td className={`text-right ${r.delta == null ? "opacity-50" :
                                              r.delta > 0 ? "text-positive" :
                                              r.delta < 0 ? "text-negative" : ""}`}>
                    {r.delta == null ? "—" :
                     `${r.delta >= 0 ? "+" : ""}£${r.delta.toFixed(2)}`}
                  </td>
                  <td className="text-xs font-mono opacity-80">
                    {Object.entries(r.by_account)
                      .map(([k, v]) => `${k}: £${Number(v).toFixed(0)}`)
                      .join(" · ")}
                  </td>
                  <td className="text-xs opacity-60 font-mono">{r.computed_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function Card({ title, value }: { title: string; value: string | number }) {
  return (
    <div className="border border-black/10 dark:border-white/10 rounded p-4">
      <div className="text-xs uppercase opacity-60 tracking-wide">{title}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
