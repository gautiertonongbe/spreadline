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

import { NOT_AVAILABLE } from "@/lib/format";

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
      {value}
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
