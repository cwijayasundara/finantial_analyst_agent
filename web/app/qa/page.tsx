"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { renderInline } from "@/lib/wikilinks";

type ToolCall = { name: string; args: unknown };
type Turn = {
  question: string;
  answer?: string;
  toolCalls: ToolCall[];
  refused: string[];
  iterations: number;
  error?: string;
};

export default function QAPage() {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask() {
    if (!question.trim()) return;
    const turn: Turn = { question, toolCalls: [], refused: [], iterations: 0 };
    setHistory((h) => [...h, turn]);
    setBusy(true);
    try {
      const out = await api.qa.askSync(question);
      setHistory((h) => h.map((t, i) => i === h.length - 1 ? {
        ...t,
        answer: out.answer,
        toolCalls: out.tool_calls,
        refused: out.refused,
        iterations: out.iterations,
      } : t));
    } catch (e) {
      setHistory((h) => h.map((t, i) => i === h.length - 1 ? {
        ...t, error: String(e),
      } : t));
    } finally {
      setBusy(false);
      setQuestion("");
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Q&amp;A</h1>
        <p className="text-sm opacity-70">
          Ask a question about your ledger. The agent reads via Cypher
          + wiki pages; writes are refused on this surface (use the CLI
          <code> merge </code> subcommand for those).
        </p>
      </header>

      <form onSubmit={(e) => { e.preventDefault(); ask(); }} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. what was my biggest dining spend in 2025_04?"
          className="flex-1 border border-black/10 dark:border-white/10 rounded px-3 py-2 bg-transparent text-sm"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !question.trim()}
          className="px-4 py-2 rounded bg-info text-white disabled:opacity-50">
          {busy ? "…" : "ask"}
        </button>
      </form>

      <ol className="space-y-4">
        {history.map((t, i) => (
          <li key={i} className="border border-black/10 dark:border-white/10 rounded p-3">
            <p className="font-medium">{t.question}</p>
            {t.error && <p className="badge badge-negative mt-2">{t.error}</p>}
            {t.toolCalls.length > 0 && (
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer opacity-60">{t.toolCalls.length} tool call(s)</summary>
                <ul className="mt-1 font-mono space-y-1">
                  {t.toolCalls.map((tc, j) => (
                    <li key={j}>{tc.name}({JSON.stringify(tc.args).slice(0, 120)})</li>
                  ))}
                </ul>
              </details>
            )}
            {t.refused.length > 0 && (
              <p className="mt-2 badge badge-warn">refused write tools: {t.refused.join(", ")}</p>
            )}
            {t.answer && (
              <div className="mt-2 prose-pfh">{renderInline(t.answer)}</div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
