import Link from "next/link";

import { api, type MemoSummary, type RecommendationRow } from "@/lib/api";

async function safeFetch<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

export default async function Dashboard() {
  const [memos, recs, health] = await Promise.all([
    safeFetch<MemoSummary[]>(api.memos.list(), []),
    safeFetch<RecommendationRow[]>(api.recommendations.list("proposed"), []),
    safeFetch(api.health(), { status: "down", host: "?", version: "?" }),
  ]);
  const latestMemo = memos[memos.length - 1];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm opacity-70">
          API status: <span className={health.status === "ok" ? "badge badge-positive" : "badge badge-negative"}>
            {health.status}
          </span>
          <span className="ml-2 font-mono opacity-60">v{health.version}</span>
        </p>
      </header>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KPI title="Memos written"        value={memos.length} href="/memos" />
        <KPI title="Open recommendations" value={recs.length} href="/recommendations" />
        <KPI title="Latest memo"
             value={latestMemo?.period ?? "—"}
             href={latestMemo ? `/memos/${latestMemo.period}` : "/memos"} />
      </section>

      {recs.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Open recommendations</h2>
          <ul className="space-y-2">
            {recs.slice(0, 5).map((r) => (
              <li key={r.id} className="border border-black/10 dark:border-white/10 rounded p-3">
                <Link href={`/recommendations/${r.id}`} className="font-medium hover:underline">
                  {r.kind.replace(/_/g, " ")} · {r.period}
                </Link>
                <span className="ml-2 badge badge-info">{r.status}</span>
                <p className="text-xs opacity-60 mt-1 font-mono">{r.id}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function KPI({ title, value, href }: { title: string; value: string | number; href: string }) {
  return (
    <Link href={href}
          className="block border border-black/10 dark:border-white/10 rounded p-4 hover:border-info">
      <div className="text-xs uppercase opacity-60 tracking-wide">{title}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </Link>
  );
}
