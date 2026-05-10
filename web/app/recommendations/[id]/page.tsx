"use client";

import { useEffect, useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { api, type WikiPage } from "@/lib/api";

export default function RecDetail({ params }: { params: Promise<{ id: string }> }) {
  const [page, setPage] = useState<WikiPage | null>(null);
  const [loading, setLoading] = useState<"accept" | "dismiss" | null>(null);
  const [error, setError] = useState<string>("");
  const [id, setId] = useState<string>("");

  useEffect(() => {
    params.then((p) => {
      setId(p.id);
      api.recommendations.get(p.id).then(setPage).catch((e) => setError(String(e)));
    });
  }, [params]);

  if (error) return <p className="badge badge-negative">{error}</p>;
  if (!page) return <p className="opacity-70">loading…</p>;

  const fm = page.frontmatter;
  const status = String(fm.status ?? "proposed");

  async function flip(verb: "accept" | "dismiss") {
    setLoading(verb);
    try {
      const fn = verb === "accept" ? api.recommendations.accept : api.recommendations.dismiss;
      await fn(id, { actor: "user", reason: "via web" });
      const fresh = await api.recommendations.get(id);
      setPage(fresh);
    } catch (e) { setError(String(e)); }
    finally { setLoading(null); }
  }

  return (
    <article className="space-y-4">
      <header>
        <p className="text-xs font-mono opacity-60">{id}</p>
        <h1 className="text-2xl font-semibold">
          {String(fm.kind ?? "").replace(/_/g, " ")} · {String(fm.period ?? "")}
        </h1>
        <div className="mt-2 flex items-center gap-2">
          <span className={`badge ${
            status === "proposed"  ? "badge-warn" :
            status === "accepted"  ? "badge-positive" :
            status === "dismissed" ? "badge-negative" : "badge-info"
          }`}>{status}</span>
          {status === "proposed" && (
            <>
              <button onClick={() => flip("accept")} disabled={!!loading}
                className="px-3 py-1.5 rounded text-sm bg-positive text-white disabled:opacity-50">
                {loading === "accept" ? "…" : "accept"}
              </button>
              <button onClick={() => flip("dismiss")} disabled={!!loading}
                className="px-3 py-1.5 rounded text-sm bg-negative text-white disabled:opacity-50">
                {loading === "dismiss" ? "…" : "dismiss"}
              </button>
            </>
          )}
        </div>
      </header>
      <MarkdownView body={page.body} />
    </article>
  );
}
