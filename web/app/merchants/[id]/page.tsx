import { notFound } from "next/navigation";

import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api";

export default async function MerchantDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let m;
  try { m = await api.merchants.get(id); } catch { notFound(); }
  if (!m) notFound();
  const fm = m.frontmatter;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
      <article>
        <header className="mb-4">
          <p className="text-xs font-mono opacity-60">{m.id}</p>
          <h1 className="text-2xl font-semibold">{String(fm.canonical_name ?? id)}</h1>
          <p className="mt-1">
            <span className="badge badge-info">{String(fm.category ?? "other")}</span>
          </p>
        </header>
        <MarkdownView body={m.body} />
      </article>
      <aside>
        <section>
          <h2 className="text-sm font-semibold uppercase opacity-60 tracking-wide mb-2">
            Recent transactions ({m.recent_transactions.length})
          </h2>
          <ul className="space-y-1 text-xs font-mono">
            {m.recent_transactions.map((t) => (
              <li key={t.id} className="flex justify-between gap-2 border-b border-black/5 dark:border-white/5 pb-1">
                <span>{t.date}</span>
                <span className="opacity-70 truncate">{t.raw_description}</span>
                <span>£{t.amount}</span>
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}
