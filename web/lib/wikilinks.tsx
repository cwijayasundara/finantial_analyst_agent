import Link from "next/link";
import type { ReactNode } from "react";

export function pageRoute(pageId: string): string {
  if (pageId.startsWith("merchant_")) return `/merchants/${pageId.slice(9)}`;
  if (pageId.startsWith("memo_"))     return `/memos/${pageId.slice(5)}`;
  if (pageId.startsWith("rec_"))      return `/recommendations/${pageId}`;
  if (pageId.startsWith("decision_")) return `/decisions/${pageId}`;
  if (pageId.startsWith("stmt_"))     return `/statements/${pageId}`;
  if (pageId.startsWith("sub_"))      return `/subscriptions/${pageId}`;
  if (pageId.startsWith("cat_"))      return `/categories/${pageId.slice(4)}`;
  if (pageId.startsWith("budget_"))   return `/budgets/${pageId}`;
  if (pageId.startsWith("acct_"))     return `/accounts/${pageId}`;
  return `/wiki/${pageId}`;
}

export function pageDisplay(pageId: string): string {
  return pageId.replace(/^[a-z]+_/, "").replace(/_/g, " ");
}

export function Wikilink({ id }: { id: string }) {
  return <Link href={pageRoute(id)} className="wikilink">{pageDisplay(id)}</Link>;
}

const PATTERN = /\[\[([a-zA-Z0-9_]+)\]\]/g;

export function renderInline(text: string): ReactNode {
  const out: ReactNode[] = [];
  let cursor = 0;
  let m: RegExpExecArray | null;
  PATTERN.lastIndex = 0;
  while ((m = PATTERN.exec(text)) !== null) {
    if (m.index > cursor) out.push(text.slice(cursor, m.index));
    out.push(<Wikilink key={`${m[1]}-${m.index}`} id={m[1]} />);
    cursor = m.index + m[0].length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}
