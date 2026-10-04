import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter, Playfair_Display } from "next/font/google";

import { THEME_KEY } from "@/lib/theme";

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

/**
 * Resolve the theme before the first paint.
 *
 * Stored choice wins, the operating system decides otherwise. This has to run
 * synchronously in the document head: deferring it to React would paint one
 * frame of the wrong theme on every page load, which is the single most visible
 * defect a theme switcher can ship with.
 */
const THEME_SCRIPT = `(function(){try{var s=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)});var d=s==="dark"||(!s&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.dataset.theme=d?"dark":"light";}catch(e){document.documentElement.dataset.theme="light";}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `data-theme` is deliberately absent from this markup and set only by the
    // script below. Rendering it here would make React own the attribute, and
    // hydration would reconcile the stored choice back to whatever the server
    // rendered: the theme would flip back to light on every page load.
    // `suppressHydrationWarning` is what lets the script own it.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${playfair.variable} ${inter.variable} ${plexMono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
