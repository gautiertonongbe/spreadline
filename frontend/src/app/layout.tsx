import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter, Playfair_Display } from "next/font/google";

import { DataSourceBanner } from "@/components/data-source-banner";
import { Nav } from "@/components/nav";

import "./globals.css";

/**
 * Typography.
 *
 * Playfair Display carries the identity: page titles, section headings and the
 * display figures. It is a high-contrast transitional serif, which is exactly
 * why it is not used for the dense numeric columns - at 11px a hairline serif
 * makes an 8 and a 3 hard to separate, and those columns are money. Those cells
 * use IBM Plex Mono with tabular figures so decimal points align down the page.
 */
const playfair = Playfair_Display({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
  weight: ["400", "500", "600", "700"],
});

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Spreadline",
  description:
    "Cross-market inventory intelligence: where is the best use of inventory capital right now.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${playfair.variable} ${inter.variable} ${plexMono.variable}`}
    >
      <body className="min-h-screen">
        <div className="flex min-h-screen">
          <aside className="hidden w-60 shrink-0 border-r border-border bg-surface lg:flex lg:flex-col">
            <div className="border-b border-hairline px-6 py-6">
              <a href="/" className="block">
                <div className="font-display text-[1.375rem] font-medium leading-none tracking-tight text-primary">
                  Spreadline
                </div>
                <div className="mt-2 text-3xs uppercase tracking-label text-faint">
                  Inventory Intelligence
                </div>
              </a>
            </div>
            <Nav />
            <div className="mt-auto border-t border-hairline px-6 py-5">
              <DataSourceBanner variant="sidebar" />
            </div>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3 lg:hidden">
              <a href="/" className="font-display text-lg font-medium tracking-tight">
                Spreadline
              </a>
              <DataSourceBanner variant="compact" />
            </header>
            <div className="border-b border-hairline lg:hidden">
              <Nav orientation="horizontal" />
            </div>
            <main className="min-w-0 flex-1 px-6 py-7 lg:px-9 lg:py-9">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
