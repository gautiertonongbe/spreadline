/**
 * Presentation primitives.
 *
 * Colour is meaning, not decoration. A recommendation, a risk level and a
 * confidence each own one colour, so a warm cell always signals the same thing
 * wherever it appears. Gold is the only non-semantic accent and is never used
 * for a result.
 */
import type { ReactNode } from "react";
import { clsx } from "clsx";

import {
  NOT_AVAILABLE,
  isOpenableUrl,
  money as formatMoney,
  percent as formatPercent,
  recommendationLabel,
} from "@/lib/format";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="display text-[1.75rem] font-medium leading-tight tracking-tight text-primary">
          {title}
        </h1>
        {description && (
          <p className="mt-1.5 max-w-2xl text-[0.8125rem] leading-relaxed text-muted">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  flush = false,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Remove body padding, for a card whose whole body is a table. */
  flush?: boolean;
}) {
  return (
    <section
      className={clsx(
        "overflow-hidden rounded-lg border border-border bg-surface shadow-card",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-hairline bg-hairline-top px-5 py-4">
          <div className="min-w-0">
            {title && (
              <h2 className="display text-[0.9375rem] font-medium leading-snug tracking-tight text-primary">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-1 text-xs leading-relaxed text-muted">{subtitle}</p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "px-5 py-4"}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
  size = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "buy" | "review" | "pass" | "muted" | "accent";
  size?: "default" | "large";
}) {
  const toneClass = {
    default: "text-primary",
    buy: "text-buy",
    review: "text-review",
    pass: "text-pass",
    muted: "text-muted",
    accent: "text-accent",
  }[tone];
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5 shadow-card">
      <div className="label">{label}</div>
      <div
        className={clsx(
          "display mt-2 font-medium leading-none tracking-tight",
          size === "large" ? "text-3xl" : "text-[1.5rem]",
          toneClass,
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-2 text-xs leading-snug text-muted">{hint}</div>}
    </div>
  );
}

const RECOMMENDATION_STYLES: Record<string, string> = {
  buy: "bg-buy/12 text-buy ring-buy/25",
  review: "bg-review/12 text-review ring-review/25",
  pass: "bg-pass/12 text-pass ring-pass/25",
};

export function RecommendationBadge({ value }: { value: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-2 py-[3px] text-3xs font-semibold uppercase tracking-label ring-1 ring-inset",
        RECOMMENDATION_STYLES[value] ?? "bg-raised text-muted ring-border",
      )}
    >
      {recommendationLabel(value)}
    </span>
  );
}

const RISK_STYLES: Record<string, string> = {
  low: "text-risk-low",
  medium: "text-risk-medium",
  high: "text-risk-high",
  critical: "text-risk-critical",
};

const RISK_DOT: Record<string, string> = {
  low: "bg-risk-low",
  medium: "bg-risk-medium",
  high: "bg-risk-high",
  critical: "bg-risk-critical",
};

/** Risk reads as a dot plus a word: legible at a glance down a column. */
export function RiskBadge({ value }: { value: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-xs capitalize",
        RISK_STYLES[value] ?? "text-muted",
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", RISK_DOT[value] ?? "bg-faint")} />
      {value}
    </span>
  );
}

/**
 * Confidence appears everywhere the platform is uncertain. It is a first class
 * part of the interface, not a footnote: a high score built on low-confidence
 * data is a different proposition from the same score built on measurement.
 */
export function ConfidenceBadge({ value, label }: { value: string; label?: string }) {
  const styles: Record<string, string> = {
    high: "text-buy",
    medium: "text-review",
    low: "text-risk-high",
    none: "text-pass",
  };
  return (
    <span
      className={clsx(
        "text-3xs font-medium uppercase tracking-label",
        styles[value] ?? "text-muted",
      )}
    >
      {label ? `${label} ` : ""}
      {value === "none" ? "no data" : value}
    </span>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "warning" | "danger" | "success";
}) {
  const tones = {
    neutral: "bg-raised text-muted ring-border",
    accent: "bg-accent/10 text-accent ring-accent/25",
    warning: "bg-review/10 text-review ring-review/25",
    danger: "bg-pass/10 text-pass ring-pass/25",
    success: "bg-buy/10 text-buy ring-buy/25",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-2 py-[3px] text-2xs font-medium ring-1 ring-inset",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function ScoreBar({ value, tone = "accent" }: { value: number; tone?: string }) {
  const width = Math.max(0, Math.min(100, value));
  const tones: Record<string, string> = {
    accent: "bg-accent",
    buy: "bg-buy",
    review: "bg-review",
    pass: "bg-pass",
  };
  return (
    <div className="h-[3px] w-full overflow-hidden rounded-full bg-overlay">
      <div
        className={clsx("h-full rounded-full transition-all", tones[tone] ?? "bg-accent")}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-6 py-16 text-center">
      <h3 className="display text-base font-medium text-primary">{title}</h3>
      <p className="mt-2 max-w-md text-[0.8125rem] leading-relaxed text-muted">
        {description}
      </p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-pass/25 bg-pass/10 px-4 py-3 text-[0.8125rem] leading-relaxed text-pass">
      {message}
    </div>
  );
}

export function Note({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "warning";
}) {
  return (
    <div
      className={clsx(
        "rounded border px-3 py-2 text-xs leading-relaxed",
        tone === "warning"
          ? "border-review/25 bg-review/10 text-review"
          : "border-border bg-raised text-muted",
      )}
    >
      {children}
    </div>
  );
}

/**
 * Renders a value that may legitimately be absent. Centralised so a null can
 * never be rendered as an empty cell that reads as zero.
 */
export function Value({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const isMissing =
    children === NOT_AVAILABLE || children === null || children === undefined;
  return (
    <span
      className={clsx(
        isMissing
          ? "font-sans text-2xs font-normal tracking-normal text-faint"
          : className,
      )}
    >
      {isMissing ? NOT_AVAILABLE : children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  type = "button",
  tone = "neutral",
  disabled = false,
  size = "default",
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  tone?: "neutral" | "accent" | "buy" | "pass";
  disabled?: boolean;
  size?: "default" | "small";
  className?: string;
}) {
  const tones = {
    neutral: "border-border bg-raised text-secondary hover:border-faint hover:text-primary",
    accent: "border-accent/30 bg-accent/10 text-accent hover:bg-accent/20",
    buy: "border-buy/30 bg-buy/10 text-buy hover:bg-buy/20",
    pass: "border-pass/30 bg-pass/10 text-pass hover:bg-pass/20",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center justify-center rounded border font-medium transition disabled:cursor-not-allowed disabled:opacity-45",
        size === "small" ? "px-2.5 py-1 text-2xs" : "px-3.5 py-[7px] text-xs",
        tones[tone],
        className,
      )}
    >
      {children}
    </button>
  );
}

/**
 * The call.
 *
 * This is the block the product is for. A reviewer reading one screen should be
 * able to answer three questions without scrolling: what is the call, what do I
 * make if it is right, and what is stopping it. Everything that explains how
 * the call was reached lives below it, folded away, because an explanation
 * nobody opens is not transparency, it is noise.
 *
 * The numbers here are always three. Four is a table, and a table is what this
 * block exists to replace.
 */
export function Verdict({
  recommendation,
  headline,
  numbers,
  blockers = [],
  meta,
  actions,
}: {
  recommendation: string;
  headline?: string | null;
  numbers: {
    label: string;
    value: ReactNode;
    hint?: string;
    tone?: "default" | "buy" | "pass" | "muted";
  }[];
  blockers?: { text: string; hard?: boolean }[];
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  const accent =
    {
      buy: "border-buy/35 bg-buy/[0.07]",
      review: "border-review/35 bg-review/[0.07]",
      pass: "border-pass/30 bg-pass/[0.05]",
    }[recommendation] ?? "border-border bg-surface";

  const word =
    { buy: "text-buy", review: "text-review", pass: "text-pass" }[recommendation] ??
    "text-primary";

  return (
    <section className={clsx("rounded-xl border shadow-card", accent)}>
      <div className="flex flex-wrap items-start justify-between gap-5 px-6 pt-6">
        <div className="min-w-0">
          <div className="label">The call</div>
          <div
            className={clsx(
              "display mt-1.5 text-[2.5rem] font-medium leading-none tracking-[-0.02em]",
              word,
            )}
          >
            {recommendationLabel(recommendation, "sentence")}
          </div>
          {headline && (
            <p className="mt-3 max-w-xl text-[0.875rem] leading-relaxed text-secondary">
              {headline}
            </p>
          )}
          {meta && <div className="mt-3 flex flex-wrap items-center gap-2">{meta}</div>}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>

      <div className="mt-6 grid gap-px border-t border-border bg-border sm:grid-cols-3">
        {numbers.map((item) => (
          <div key={item.label} className="bg-surface px-6 py-5">
            <div className="label">{item.label}</div>
            <div
              className={clsx(
                "display mt-2 text-[1.75rem] font-medium leading-none tracking-tight",
                {
                  default: "text-primary",
                  buy: "text-buy",
                  pass: "text-pass",
                  muted: "text-muted",
                }[item.tone ?? "default"],
              )}
            >
              {item.value}
            </div>
            {item.hint && (
              <div className="mt-2 text-2xs leading-snug text-muted">{item.hint}</div>
            )}
          </div>
        ))}
      </div>

      {blockers.length > 0 && (
        <div className="space-y-1.5 border-t border-border px-6 py-4">
          <div className="label">What is stopping it</div>
          {blockers.slice(0, 3).map((blocker) => (
            <div key={blocker.text} className="flex gap-2.5 text-[0.8125rem] leading-snug">
              <span
                className={clsx(
                  "mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full",
                  blocker.hard ? "bg-pass" : "bg-review",
                )}
              />
              <span className="text-secondary">{blocker.text}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export interface MarketSideView {
  marketplace: string;
  price: string | null;
  title?: string | null;
  externalId?: string | null;
  availability?: string | null;
  sellerCount?: number | null;
  salesRank?: number | null;
  url?: string | null;
}

/**
 * The two markets, side by side, with the identity between them.
 *
 * This strip is the reason the product exists. Answering "what does it cost
 * there, what does it sell for here, and are they the same thing" normally
 * means a retailer tab, a marketplace tab, a rank history tab and a fee
 * calculator, and the answer assembled that way is a guess about two similar
 * titles. The confidence figure in the middle is what makes the comparison
 * legitimate, which is why it sits between the two prices rather than in a
 * panel further down.
 */
export function MarketStrip({
  source,
  target,
  matchConfidence,
}: {
  source: MarketSideView | null;
  target: MarketSideView | null;
  matchConfidence: string | null;
}) {
  const live = isOpenableUrl(source?.url) || isOpenableUrl(target?.url);
  const side = (
    role: string,
    caption: string,
    action: string,
    listing: MarketSideView | null,
  ) => (
    <div className="flex flex-col bg-surface px-5 py-4">
      <div className="label">{role}</div>
      <div className="mt-1.5 text-[0.8125rem] font-medium text-primary">
        {listing?.marketplace ?? "not available"}
      </div>
      <div className="numeric mt-2 text-[1.75rem] leading-none text-primary">
        <Value>{listing ? formatMoney(listing.price) : NOT_AVAILABLE}</Value>
      </div>
      <div className="mt-1 text-2xs text-faint">{caption}</div>
      {listing?.title && (
        <div className="mt-2.5 truncate text-xs text-muted">{listing.title}</div>
      )}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-faint">
        {listing?.externalId && <span className="font-mono">{listing.externalId}</span>}
        {listing?.availability && <span>{listing.availability.replace(/_/g, " ")}</span>}
        {listing?.sellerCount !== null && listing?.sellerCount !== undefined && (
          <span>{listing.sellerCount} sellers</span>
        )}
        {listing?.salesRank ? <span>Rank {listing.salesRank.toLocaleString()}</span> : null}
      </div>
      {isOpenableUrl(listing?.url) ? (
        <a
          href={listing.url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-3.5 inline-flex w-fit items-center gap-1.5 rounded border border-accent/30 bg-accent/10 px-3 py-1.5 text-2xs font-medium text-accent transition hover:bg-accent/20"
        >
          {action}
          <span aria-hidden="true">&rarr;</span>
        </a>
      ) : (
        <span className="mt-3.5 inline-flex w-fit items-center rounded border border-dashed border-border px-3 py-1.5 text-2xs text-faint">
          No live listing to open
        </span>
      )}
    </div>
  );

  return (
    <div>
      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border shadow-card sm:grid-cols-[1fr_150px_1fr]">
        {side(
          "Buy it here",
          "what you pay today",
          `Open on ${source?.marketplace ?? "the site"}`,
          source,
        )}
        <div className="flex flex-col items-center justify-center bg-surface px-4 py-4 text-center">
          <div className="label">Same item?</div>
          <div className="numeric mt-1.5 text-base text-primary">
            {formatPercent(matchConfidence, 0)}
          </div>
          <div className="mt-1 text-3xs uppercase tracking-label text-faint">sure</div>
        </div>
        {side(
          "Sell it here",
          "what it sells for today",
          `Open on ${target?.marketplace ?? "the site"}`,
          target,
        )}
      </div>
      {/* Asked often enough to belong on the screen rather than in a help page.
          Spreadline does not automate retailer checkout, by design and by
          policy, so the order is placed on the retailer's own site. */}
      <p className="mt-2 text-2xs leading-relaxed text-faint">
        {live
          ? "Spreadline does not place orders. Open the listing and buy it on the retailer's own site, then come back and record what you paid so the prediction can be checked against what actually happened."
          : "These are fixture products, so there is no real listing behind either price. Connect a provider and the links open the retailer's own page, where the order is placed: Spreadline does not place orders itself."}
      </p>
    </div>
  );
}

/**
 * Progressive disclosure, built on a native `details` element.
 *
 * Everything the platform computed is still on the page and one click away.
 * Nothing is removed, nothing is summarised away, and none of it loads behind a
 * request that could fail. It just starts closed, because the score breakdown
 * matters to the person auditing a call and not to the person making one.
 */
export function Disclosure({
  title,
  summary,
  children,
  defaultOpen = false,
}: {
  title: string;
  /** One line, visible while closed: enough to know whether to open it. */
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details
      open={defaultOpen}
      className="group overflow-hidden rounded-lg border border-border bg-surface shadow-card"
    >
      <summary className="flex cursor-pointer select-none items-center gap-3 px-5 py-3.5 transition hover:bg-raised/60">
        <svg
          viewBox="0 0 12 12"
          width="10"
          height="10"
          fill="none"
          aria-hidden="true"
          className="shrink-0 text-faint transition-transform duration-150 group-open:rotate-90"
        >
          <path
            d="M4 2.5 8 6l-4 3.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="display text-[0.9375rem] font-medium tracking-tight text-primary">
          {title}
        </span>
        {summary && (
          <span className="ml-auto truncate text-2xs text-muted group-open:hidden">
            {summary}
          </span>
        )}
      </summary>
      <div className="border-t border-hairline px-5 py-4">{children}</div>
    </details>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] border-collapse text-[0.8125rem]">
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  align = "left",
  className,
}: {
  children?: ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
}) {
  return (
    <th
      className={clsx(
        "border-b border-border bg-surface px-3 py-2.5 text-3xs font-semibold uppercase tracking-label text-faint",
        align === "right" && "text-right",
        align === "center" && "text-center",
        align === "left" && "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  numeric = false,
  className,
}: {
  children?: ReactNode;
  align?: "left" | "right" | "center";
  numeric?: boolean;
  className?: string;
}) {
  return (
    <td
      className={clsx(
        "border-b border-hairline px-3 py-2.5 align-middle",
        numeric && "numeric",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
    >
      {children}
    </td>
  );
}

export function Tr({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <tr className={clsx("transition-colors hover:bg-raised/40", className)}>{children}</tr>
  );
}
