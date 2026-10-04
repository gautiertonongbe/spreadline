"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";

const SECTIONS: { label: string; items: { href: string; label: string }[] }[] = [
  {
    label: "Decide",
    items: [
      { href: "/", label: "Dashboard" },
      { href: "/analyze", label: "Analyze" },
      { href: "/opportunities", label: "Opportunities" },
    ],
  },
  {
    label: "Deploy",
    items: [
      { href: "/bulk", label: "Bulk analysis" },
      { href: "/capital", label: "Capital" },
      { href: "/execution", label: "Buy and record" },
    ],
  },
  {
    label: "Autonomy",
    items: [
      { href: "/autonomy", label: "Overview" },
      { href: "/autonomy/allocation", label: "What to buy" },
      { href: "/autonomy/positions", label: "Positions" },
      { href: "/autonomy/sell", label: "What to sell" },
      { href: "/autonomy/decisions", label: "Decisions" },
      { href: "/autonomy/backtest", label: "Replay a policy" },
      { href: "/autonomy/learning", label: "What it gets wrong" },
      { href: "/autonomy/events", label: "Audit log" },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/history", label: "Observation history" },
      { href: "/providers", label: "Providers" },
    ],
  },
];

export function Nav({ orientation = "vertical" }: { orientation?: "vertical" | "horizontal" }) {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  if (orientation === "horizontal") {
    return (
      <nav className="flex gap-1 overflow-x-auto px-4 py-2">
        {SECTIONS.flatMap((section) => section.items.slice(0, section.label === "Autonomy" ? 1 : section.items.length)).map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={clsx(
              "whitespace-nowrap rounded px-3 py-1.5 text-xs transition",
              isActive(item.href)
                ? "bg-raised text-primary"
                : "text-muted hover:text-secondary",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    );
  }

  return (
    <nav className="flex-1 px-4 py-5">
      {SECTIONS.map((section) => (
        <div key={section.label} className="mb-6">
          <div className="px-2 pb-2 text-3xs uppercase tracking-label text-faint">
            {section.label}
          </div>
          {section.items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "relative block rounded px-2 py-[7px] text-[0.8125rem] transition",
                isActive(item.href)
                  ? "bg-raised text-primary"
                  : "text-muted hover:bg-raised/50 hover:text-secondary",
              )}
            >
              {isActive(item.href) && (
                <span className="absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-accent" />
              )}
              {item.label}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
