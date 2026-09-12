import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "Spreadline",
  description:
    "Cross-market inventory intelligence: where is the best use of inventory capital right now.",
};

const NAVIGATION = [
  { href: "/", label: "Dashboard" },
  { href: "/analyze", label: "Analyze" },
  { href: "/opportunities", label: "Opportunities" },
  { href: "/bulk", label: "Bulk" },
  { href: "/capital", label: "Capital" },
  { href: "/providers", label: "Providers" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <div className="flex min-h-screen">
          <aside className="hidden w-56 shrink-0 border-r border-border bg-surface lg:block">
            <div className="px-5 py-5">
              <Link href="/" className="block">
                <div className="text-sm font-semibold tracking-tight text-primary">
                  Spreadline
                </div>
                <div className="mt-0.5 text-2xs text-muted">
                  Inventory intelligence
                </div>
              </Link>
            </div>
            <nav className="px-3 pb-6">
              {NAVIGATION.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="block rounded px-3 py-2 text-sm text-muted transition hover:bg-raised hover:text-primary"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex items-center justify-between border-b border-border bg-surface px-6 py-3 lg:hidden">
              <Link href="/" className="text-sm font-semibold">
                Spreadline
              </Link>
              <nav className="flex gap-3 overflow-x-auto text-xs text-muted">
                {NAVIGATION.map((item) => (
                  <Link key={item.href} href={item.href} className="whitespace-nowrap">
                    {item.label}
                  </Link>
                ))}
              </nav>
            </header>
            <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
