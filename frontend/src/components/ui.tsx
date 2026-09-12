/**
 * Shared presentation primitives.
 *
 * Colour is meaning here, not decoration: a recommendation, a risk level and a
 * confidence each have one fixed colour so that a red cell always signals the
 * same thing wherever it appears.
 */
import type { ReactNode } from "react";
import { clsx } from "clsx";

import { NOT_AVAILABLE } from "@/lib/format";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={clsx(
        "rounded-lg border border-border bg-surface",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold text-primary">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "buy" | "review" | "pass" | "muted";
}) {
  const toneClass = {
    default: "text-primary",
    buy: "text-buy",
    review: "text-review",
    pass: "text-pass",
    muted: "text-muted",
  }[tone];
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <div className="text-2xs uppercase tracking-wide text-muted">{label}</div>
      <div className={clsx("numeric mt-1.5 text-xl font-semibold", toneClass)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const RECOMMENDATION_STYLES: Record<string, string> = {
  buy: "bg-buy/15 text-buy border-buy/30",
  review: "bg-review/15 text-review border-review/30",
  pass: "bg-pass/15 text-pass border-pass/30",
};

export function RecommendationBadge({ value }: { value: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded border px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide",
        RECOMMENDATION_STYLES[value] ?? "border-border bg-raised text-muted",
      )}
    >
      {value}
    </span>
  );
}

const RISK_STYLES: Record<string, string> = {
  low: "bg-risk-low/15 text-risk-low border-risk-low/30",
  medium: "bg-risk-medium/15 text-risk-medium border-risk-medium/30",
  high: "bg-risk-high/15 text-risk-high border-risk-high/30",
  critical: "bg-risk-critical/15 text-risk-critical border-risk-critical/30",
};

export function RiskBadge({ value }: { value: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded border px-2 py-0.5 text-2xs font-medium capitalize",
        RISK_STYLES[value] ?? "border-border bg-raised text-muted",
      )}
    >
      {value}
    </span>
  );
}

/**
 * Confidence is rendered everywhere the platform is uncertain. It is a first
 * class part of the interface, not a footnote, because a high score built on
 * low-confidence data is a different proposition from the same score built on
 * measured data.
 */
export function ConfidenceBadge({ value, label }: { value: string; label?: string }) {
  const styles: Record<string, string> = {
    high: "text-buy",
    medium: "text-review",
    low: "text-risk-high",
    none: "text-pass",
  };
  return (
    <span className={clsx("text-2xs font-medium uppercase tracking-wide", styles[value] ?? "text-muted")}>
      {label ? `${label}: ` : ""}
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
    neutral: "border-border bg-raised text-muted",
    accent: "border-accent/30 bg-accent/10 text-accent",
    warning: "border-review/30 bg-review/10 text-review",
    danger: "border-pass/30 bg-pass/10 text-pass",
    success: "border-buy/30 bg-buy/10 text-buy",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded border px-2 py-0.5 text-2xs font-medium",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

/** A 0-100 bar. Used for scores and component breakdowns. */
export function ScoreBar({ value, tone = "accent" }: { value: number; tone?: string }) {
  const width = Math.max(0, Math.min(100, value));
  const tones: Record<string, string> = {
    accent: "bg-accent",
    buy: "bg-buy",
    review: "bg-review",
    pass: "bg-pass",
  };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
      <div
        className={clsx("h-full rounded-full", tones[tone] ?? "bg-accent")}
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
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-6 py-14 text-center">
      <h3 className="text-sm font-semibold text-primary">{title}</h3>
      <p className="mt-1.5 max-w-md text-sm text-muted">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-pass/30 bg-pass/10 px-4 py-3 text-sm text-pass">
      {message}
    </div>
  );
}

/**
 * Renders a value that may legitimately be absent. Centralised so that a null
 * can never be accidentally rendered as an empty cell that reads as zero.
 */
export function Value({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const isMissing = children === NOT_AVAILABLE || children === null || children === undefined;
  return (
    <span
      className={clsx(
        isMissing ? "font-sans text-xs font-normal normal-case text-muted" : className,
      )}
    >
      {isMissing ? NOT_AVAILABLE : children}
    </span>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right" | "center";
}) {
  return (
    <th
      className={clsx(
        "border-b border-border px-3 py-2.5 text-2xs font-semibold uppercase tracking-wide text-muted",
        align === "right" && "text-right",
        align === "center" && "text-center",
        align === "left" && "text-left",
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
        "border-b border-border/60 px-3 py-2.5",
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
