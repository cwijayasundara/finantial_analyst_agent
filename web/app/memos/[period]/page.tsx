import { notFound } from "next/navigation";

import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api";
import { Wikilink } from "@/lib/wikilinks";

export default async function MemoDetail({ params }: { params: Promise<{ period: string }> }) {
  const { period } = await params;
  let page;
  try { page = await api.memos.get(period); } catch { notFound(); }
  if (!page) notFound();
  const cites = (page.frontmatter.cites as string[] | undefined) ?? [];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_240px] gap-6">
      <article>
        <header className="mb-4">
          <p className="text-xs opacity-60 font-mono">{page.id}</p>
          <h1 className="text-2xl font-semibold">Memo · {String(page.frontmatter.period ?? period)}</h1>
        </header>
        <MarkdownView body={page.body} />
      </article>
      <aside className="space-y-4">
        <section>
          <h2 className="text-sm font-semibold uppercase opacity-60 tracking-wide">Citations</h2>
          <ul className="mt-2 space-y-1">
            {cites.map((c) => (<li key={c}><Wikilink id={c} /></li>))}
          </ul>
        </section>
        <section>
          <h2 className="text-sm font-semibold uppercase opacity-60 tracking-wide">Confidence</h2>
          <p className="mt-1 font-mono">{String(page.frontmatter.confidence ?? "—")}</p>
        </section>
      </aside>
    </div>
  );
}
