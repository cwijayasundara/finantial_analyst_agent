import { api } from "@/lib/api";

export default async function BudgetsPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const sp = await searchParams;
  const period = sp.period;
  const [budgets, variances] = await Promise.all([
    api.budgets.list(period),
    period ? api.budgets.variance(period).catch(() => []) : Promise.resolve([]),
  ]);

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Budgets</h1>
      <form action="" method="GET" className="mb-4 flex gap-2">
        <input
          name="period" defaultValue={period ?? ""}
          placeholder="period (yyyy_mm)"
          className="border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm"
        />
        <button type="submit" className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5">
          show
        </button>
      </form>
      <table className="w-full text-sm">
        <thead className="text-left opacity-60 text-xs uppercase tracking-wide">
          <tr>
            <th className="py-2">period</th>
            <th>scope</th>
            <th className="text-right">target</th>
            <th className="text-right">actual</th>
            <th>flag</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-black/10 dark:divide-white/10">
          {budgets.map((b) => {
            const v = variances.find((x) => x.budget_id === b.id);
            return (
              <tr key={b.id}>
                <td className="py-2 font-mono">{b.period}</td>
                <td>{b.scope_type}/<code>{b.scope_id}</code></td>
                <td className="text-right">£{b.target_amount}</td>
                <td className="text-right">{v ? `£${v.actual}` : "—"}</td>
                <td>{v
                  ? <span className={`badge badge-${v.flag === "over" ? "negative" : v.flag === "under" ? "positive" : "info"}`}>{v.flag}</span>
                  : <span className="opacity-50">—</span>}
                </td>
              </tr>
            );
          })}
          {budgets.length === 0 && (
            <tr><td colSpan={5} className="py-3 opacity-60">No budgets configured.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
