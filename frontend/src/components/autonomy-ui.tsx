/**
 * Presentation for the autonomy layer.
 *
 * The recurring idea: a limit is only meaningful next to what was measured
 * against it, and a metric is only meaningful next to its sample. Nothing here
 * renders a number on its own.
 */
import type { ReactNode } from "react";
import { clsx } from "clsx";

import { Value } from "@/components/ui";
import type { GateEvaluation, PerformanceMetric } from "@/lib/api";
import { money, percent, titleCase } from "@/lib/format";

const LEVEL_TONE: Record<number, string> = {
  0: "text-muted",
  1: "text-muted",
  2: "text-secondary",
  3: "text-accent",
  4: "text-accent",
  5: "text-accent",
  6: "text-buy",
  7: "text-buy",
};

/** The current level, stated as a position rather than a number. */
export function LevelBadge({
  level,
  label,
  mode,
}: {
  level: number;
  label: string;
  mode?: string;
}) {
  return (
    <span className="inline-flex items-baseline gap-2">
      <span className={clsx("display text-[1.125rem] font-medium", LEVEL_TONE[level])}>
        Level {level}
      </span>
      <span className="text-xs text-muted">{label}</span>
      {mode && (
        <span className="text-2xs uppercase tracking-label text-faint">{mode} mode</span>
      )}
    </span>
  );
}

const VERDICT_STYLE: Record<string, string> = {
  proceed: "text-buy",
  review: "text-review",
  reject: "text-pass",
  inconclusive: "text-muted",
};

/** One stage's conclusion. Shown per stage so disagreement is visible. */
export function VerdictDot({ verdict, label }: { verdict: string; label?: string }) {
  const dot: Record<string, string> = {
    proceed: "bg-buy",
    review: "bg-review",
    reject: "bg-pass",
    inconclusive: "bg-faint",
  };
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-2xs">
      <span className={clsx("h-1.5 w-1.5 rounded-full", dot[verdict] ?? "bg-faint")} />
      <span className={VERDICT_STYLE[verdict] ?? "text-muted"}>
        {label ? `${label}: ` : ""}
        {verdict}
      </span>
    </span>
  );
}

const OUTCOME_STYLE: Record<string, string> = {
  authorized: "bg-buy/12 text-buy ring-buy/25",
  blocked: "bg-pass/12 text-pass ring-pass/25",
  escalated: "bg-review/12 text-review ring-review/25",
};

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const words: Record<string, string> = {
    authorized: "Authorised",
    blocked: "Blocked",
    escalated: "Escalated",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-2 py-[3px] text-3xs font-semibold uppercase tracking-label ring-1 ring-inset",
        OUTCOME_STYLE[outcome] ?? "bg-raised text-muted ring-border",
      )}
    >
      {words[outcome] ?? outcome}
    </span>
  );
}

/** Formats a metric by its own unit, and never invents a value. */
export function metricDisplay(metric: PerformanceMetric): ReactNode {
  if (metric.value === null) return <Value>{null}</Value>;
  if (metric.unit === "money") return money(metric.value);
  if (metric.unit === "days") return `${Number(metric.value).toFixed(1)} days`;
  if (metric.unit === "count") return Number(metric.value).toFixed(0);
  return percent(metric.value);
}

/**
 * One measurement with its sample.
 *
 * The sample is not decoration: a ratio over four decisions is not a fact about
 * the system, and showing it without the count invites exactly that reading.
 */
export function MetricCard({ metric }: { metric: PerformanceMetric }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5 shadow-card">
      <div className="label">{metric.label}</div>
      <div
        className={clsx(
          "display mt-2 text-[1.5rem] font-medium leading-none tracking-tight",
          metric.value === null ? "text-muted" : "text-primary",
        )}
      >
        {metricDisplay(metric)}
      </div>
      <div className="mt-2 text-2xs text-faint">
        {metric.sample === 0
          ? "nothing measured yet"
          : `${metric.sample} observation(s)${
              metric.is_meaningful ? "" : ", not yet a meaningful sample"
            }`}
      </div>
      <div className="mt-1 text-2xs leading-snug text-muted">{metric.detail}</div>
    </div>
  );
}

/** What the next level requires, and what is still missing. */
export function GatePanel({ gate }: { gate: GateEvaluation }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="label">Next level</div>
          <div className="display mt-1 text-[1.125rem] font-medium tracking-tight text-primary">
            Level {gate.target_level}, {money(gate.target_capital_limit)}
          </div>
        </div>
        <span
          className={clsx(
            "text-2xs font-medium uppercase tracking-label",
            gate.eligible ? "text-buy" : "text-muted",
          )}
        >
          {gate.eligible ? "eligible" : "not yet eligible"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-muted">{gate.description}</p>
      <div className="space-y-1.5">
        {gate.criteria.map((criterion) => (
          <div key={criterion.key} className="flex items-start gap-2.5 text-2xs">
            <span
              className={clsx(
                "mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full",
                criterion.met ? "bg-buy" : "bg-faint",
              )}
            />
            <span className={criterion.met ? "text-muted" : "text-secondary"}>
              {criterion.note}
            </span>
          </div>
        ))}
      </div>
      <p className="text-2xs leading-relaxed text-faint">
        Meeting these makes the level available. Authorising it is still a person&apos;s
        decision: nothing here promotes itself.
      </p>
    </div>
  );
}

/** A policy limit next to what it allows, so the rule reads as a rule. */
export function LimitRow({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-hairline py-2 last:border-b-0">
      <div className="min-w-0">
        <div className="text-[0.8125rem] text-secondary">{label}</div>
        {hint && <div className="text-2xs text-faint">{hint}</div>}
      </div>
      <div className="numeric shrink-0 text-[0.8125rem] text-primary">{value}</div>
    </div>
  );
}

export function stageLabel(stage: string): string {
  return titleCase(stage);
}

/**
 * A breaker figure, read by its own unit.
 *
 * A threshold of "0.05" and one of "50" are read completely differently, and
 * the number alone does not say which it is.
 */
export function breakerValue(value: string | null, unit: string): string {
  if (value === null) return "no limit";
  if (unit === "ratio") return percent(value, 1);
  if (unit === "count") return Number(value).toFixed(0);
  return money(value);
}
