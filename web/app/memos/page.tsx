import Link from "next/link";

import { api } from "@/lib/api";

export default async function MemosPage() {
  const memos = await api.memos.list();
  if (!memos.length) {
    return <p className="opacity-70">No memos yet. Run <code>python -m cookbooks.monthly_analyst backfill-memos &lt;from&gt; &lt;to&gt;</code>.</p>;
  }
  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Memos ({memos.length})</h1>
      <ul className="divide-y divide-black/10 dark:divide-white/10 border border-black/10 dark:border-white/10 rounded">
        {memos.map((m) => (
          <li key={m.page_id} className="p-3 hover:bg-black/5 dark:hover:bg-white/5">
            <Link href={`/memos/${m.period}`} className="block">
              <span className="font-medium">{m.period}</span>
              <span className="ml-2 text-xs opacity-60 font-mono">
                {m.citations_count} citations · updated {m.updated.slice(0, 10)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
