import Link from "next/link";

import "./globals.css";

export const metadata = {
  title: "personal-finance-helper",
  description: "Local-only financial dashboard.",
};

const NAV = [
  { href: "/",                title: "Dashboard"       },
  { href: "/memos",           title: "Memos"           },
  { href: "/merchants",       title: "Merchants"       },
  { href: "/recommendations", title: "Recs"            },
  { href: "/budgets",         title: "Budgets"         },
  { href: "/goals",           title: "Goals"           },
  { href: "/networth",        title: "Net Worth"       },
  { href: "/forecast",        title: "Forecast"        },
  { href: "/qa",              title: "Q&A"             },
  { href: "/graph",           title: "Graph"           },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-paper text-ink dark:bg-ink dark:text-paper min-h-screen">
        <header className="border-b border-black/10 dark:border-white/10">
          <div className="mx-auto max-w-6xl px-4 py-3 flex items-center gap-6">
            <Link href="/" className="font-mono font-semibold">pfh</Link>
            <nav className="flex gap-4 text-sm">
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} className="hover:underline">{n.title}</Link>
              ))}
            </nav>
            <span className="ml-auto text-xs font-mono opacity-60">127.0.0.1 · local</span>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
