import Link from "next/link";

import { api } from "@/lib/api";

export default async function MerchantsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; q?: string }>;
}) {
  const params = await searchParams;
  const merchants = await api.merchants.list({
    category: params.category, q: params.q, limit: 200,
  });
  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Merchants ({merchants.length})</h1>
      <form action="" method="GET" className="mb-4 flex gap-2">
        <input
          name="q" defaultValue={params.q ?? ""} placeholder="search canonical name…"
          className="flex-1 border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm"
        />
        <input
          name="category" defaultValue={params.category ?? ""} placeholder="category"
          className="w-40 border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm"
        />
        <button type="submit" className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5">
          filter
        </button>
      </form>
      <table className="w-full text-sm">
        <thead className="text-left opacity-60 text-xs uppercase tracking-wide">
          <tr><th className="py-2">id</th><th>canonical</th><th>category</th><th className="text-right">txns</th></tr>
        </thead>
        <tbody className="divide-y divide-black/10 dark:divide-white/10">
          {merchants.map((m) => (
            <tr key={m.id} className="hover:bg-black/5 dark:hover:bg-white/5">
              <td className="py-2 font-mono text-xs"><Link href={`/merchants/${m.id}`} className="hover:underline">{m.id}</Link></td>
              <td>{m.canonical_name}</td>
              <td><span className="badge badge-info">{m.category ?? "—"}</span></td>
              <td className="text-right font-mono">{m.txn_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
