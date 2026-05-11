import { Sparkline } from "@/components/Sparkline";
import { api, type CategoryForecast } from "@/lib/api";

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

const METHOD_LABEL: Record<string, string> = {
  seasonal_naive:    "seasonal (prior year)",
  holt_smoothing:    "Holt (level + trend)",
  linear_projection: "linear fit",
  mean:              "mean",
};

const METHOD_BADGE: Record<string, string> = {
  seasonal_naive:    "badge-accent",
  holt_smoothing:    "badge-info",
  linear_projection: "badge-warn",
  mean:              "badge-positive",
};

export default async function ForecastPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string; horizon?: string; lookback?: string }>;
}) {
  const sp = await searchParams;
  const period = sp.period ?? defaultPeriod();
  const horizon = Number(sp.horizon ?? 3);
  const lookback = Number(sp.lookback ?? 12);

  const rows = await safe<CategoryForecast[]>(
    api.forecast.listCategories({ period, horizon, lookback, top_n: 12 }),
    [],
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Forecast</h1>
        <p className="text-sm opacity-70">
          Next-{horizon}-month projection per category. The advisor's{" "}
          <code>forecast_overshoot</code> recommendation uses the same
          series to flag budgets heading for a breach.
        </p>
      </header>

      <form action="" method="GET" className="flex flex-wrap gap-2 items-end">
        <Field name="period"   label="as-of period"   placeholder="yyyy_mm" defaultValue={period} />
        <Field name="horizon"  label="horizon (mo)"   defaultValue={String(horizon)}  type="number" />
        <Field name="lookback" label="lookback (mo)"  defaultValue={String(lookback)} type="number" />
        <button type="submit" className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5">
          refresh
        </button>
      </form>

      {rows.length === 0 ? (
        <p className="opacity-70">No data for <code>{period}</code>. Run the analyst first.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left opacity-60 text-xs uppercase tracking-wide">
            <tr>
              <th className="py-2">category</th>
              <th>history + forecast</th>
              <th className="text-right">avg</th>
              <th className="text-right">next 3</th>
              <th>method</th>
              <th className="text-right">RMSE</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-black/10 dark:divide-white/10">
            {rows.map((f) => (
              <tr key={f.category}>
                <td className="py-2 font-mono">{f.category}</td>
                <td><Sparkline history={f.history} forecast={f.forecast} /></td>
                <td className="text-right">£{f.monthly_average}</td>
                <td className="text-right text-xs font-mono">
                  {f.forecast.map((v) => `£${v}`).join(" · ")}
                </td>
                <td>
                  <span className={`badge ${METHOD_BADGE[f.method] ?? "badge-info"}`}>
                    {METHOD_LABEL[f.method] ?? f.method}
                  </span>
                </td>
                <td className="text-right font-mono">£{f.rmse}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Field({ name, label, defaultValue, placeholder, type = "text" }: {
  name: string; label: string; defaultValue?: string; placeholder?: string; type?: string;
}) {
  return (
    <div>
      <label className="block text-xs uppercase opacity-60 mb-1">{label}</label>
      <input
        name={name} type={type} defaultValue={defaultValue} placeholder={placeholder}
        className="border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm w-32"
      />
    </div>
  );
}

function defaultPeriod(): string {
  const d = new Date();
  return `${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, "0")}`;
}
