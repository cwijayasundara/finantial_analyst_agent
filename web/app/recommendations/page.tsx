import Link from "next/link";

import { api, type RecommendationRow } from "@/lib/api";

const TABS = [
  { key: "proposed",  label: "Proposed"  },
  { key: "accepted",  label: "Accepted"  },
  { key: "dismissed", label: "Dismissed" },
];

export default async function RecsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const sp = await searchParams;
  const status = sp.status ?? "proposed";
  const rows = await api.recommendations.list(status);
  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Recommendations</h1>
      <nav className="flex gap-2 mb-4">
        {TABS.map((t) => (
          <Link key={t.key} href={`/recommendations?status=${t.key}`}
                className={`px-3 py-1.5 rounded text-sm ${status === t.key ? "bg-info text-white" : "border border-black/10 dark:border-white/10"}`}>
            {t.label}
          </Link>
        ))}
      </nav>
      {rows.length === 0
        ? <p className="opacity-70">No recommendations with status <code>{status}</code>.</p>
        : <ul className="space-y-2">{rows.map((r) => <RecCard key={r.id} r={r} />)}</ul>
      }
    </div>
  );
}

function RecCard({ r }: { r: RecommendationRow }) {
  return (
    <li className="border border-black/10 dark:border-white/10 rounded p-3">
      <div className="flex items-center justify-between">
        <Link href={`/recommendations/${r.id}`} className="font-medium hover:underline">
          {r.kind.replace(/_/g, " ")} · {r.period}
        </Link>
        <span className={`badge ${
          r.status === "proposed"  ? "badge-warn" :
          r.status === "accepted"  ? "badge-positive" :
          r.status === "dismissed" ? "badge-negative" : "badge-info"
        }`}>{r.status}</span>
      </div>
      <p className="mt-1 text-xs font-mono opacity-60">{r.id}</p>
      {!!r.cites.length && (
        <p className="mt-1 text-xs">
          cites: {r.cites.slice(0, 3).map((c) => <code key={c} className="mr-2">{c}</code>)}
        </p>
      )}
    </li>
  );
}
