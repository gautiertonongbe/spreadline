/**
 * The evidence a recommendation rests on.
 *
 * Each block here answers one of the questions the platform exists to answer,
 * and each one shows the figures it was computed from rather than asserting a
 * conclusion. Where a value is unavailable it says so; nothing here fills a gap
 * with a plausible number.
 */
import type { ReactNode } from "react";
import { clsx } from "clsx";

import { ConfidenceBadge, RiskBadge, ScoreBar, Value } from "@/components/ui";
import type {
  Economics,
  RiskAssessment,
  RiskCategoryAssessment,
  ScoreBreakdown,
  SpreadEvidence,
} from "@/lib/api";
import { money, percent, score as formatScore, titleCase } from "@/lib/format";

/**
 * The whole chain from what it costs to what is left.
 *
 * Shown in full and in order, because the two ends on their own ask the reader
 * to take the middle on trust. Every row is a stored figure, and the rows add
 * up: landed cost plus other costs plus fees is the total, and the sale price
 * less the total is the profit.
 */
export function SpreadLadder({
  economics,
  sourceMarketplace,
  targetMarketplace,
}: {
  economics: Economics;
  sourceMarketplace: string;
  targetMarketplace: string;
}) {
  const rows: {
    label: string;
    value: ReactNode;
    hint?: string;
    tone?: "default" | "buy" | "pass" | "muted";
    rule?: boolean;
  }[] = [
    {
      label: `Buy on ${titleCase(sourceMarketplace)}`,
      value: money(economics.acquisition_cost),
      hint: "the listed price where you source it",
    },
    {
      label: "Getting it to the shelf",
      value: money(economics.other_unit_costs ?? null),
      hint: "inbound shipping, tax and any costs you entered",
      tone: "muted",
    },
    {
      label: `Sell on ${titleCase(targetMarketplace)}`,
      value: money(economics.sale_price),
      hint: "the price it currently sells for",
      rule: true,
    },
    {
      label: "Gross spread",
      value: money(economics.gross_spread ?? economics.spread ?? null),
      hint: "selling price less what you paid, before any fee",
    },
    {
      label: "Gross spread %",
      value: <Value>{percent(economics.gross_spread_pct ?? null)}</Value>,
      hint: "the gap measured against what you paid",
    },
    {
      label: "Marketplace fees",
      value: money(economics.total_fees),
      hint: "referral, fulfilment, closing, storage and returns",
      tone: "muted",
      rule: true,
    },
    {
      label: "Total cost",
      value: money(economics.total_cost),
      hint: "everything you pay, per item",
    },
    {
      label: "Net profit",
      value: money(economics.net_profit),
      hint: "what is actually left",
      tone: Number(economics.net_profit) >= 0 ? "buy" : "pass",
    },
    {
      label: "Return on your money",
      value: <Value>{percent(economics.roi)}</Value>,
      hint: "profit against the capital you put in",
    },
    {
      label: "Margin",
      value: <Value>{percent(economics.margin)}</Value>,
      hint: "profit as a share of the selling price",
    },
    {
      label: "Most you should pay",
      value: <Value>{money(economics.max_acquisition_cost)}</Value>,
      hint: "above this the trade stops making money",
      rule: true,
    },
  ];

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface shadow-card">
      <header className="border-b border-hairline bg-hairline-top px-5 py-3.5">
        <h2 className="display text-[0.9375rem] font-medium tracking-tight text-primary">
          The numbers, in order
        </h2>
        <p className="mt-1 text-xs text-muted">
          Every figure below is stored with the decision. They add up: what you pay,
          plus costs, plus fees, is the total.
        </p>
      </header>
      <dl className="divide-y divide-hairline">
        {rows.map((row) => (
          <div
            key={row.label}
            className={clsx(
              "flex items-baseline justify-between gap-6 px-5 py-2.5",
              row.rule && "border-t border-border",
            )}
          >
            <div className="min-w-0">
              <dt className="text-[0.8125rem] text-secondary">{row.label}</dt>
              {row.hint && <div className="text-2xs text-faint">{row.hint}</div>}
            </div>
            <dd
              className={clsx(
                "numeric shrink-0 text-[0.9375rem]",
                {
                  default: "text-primary",
                  buy: "text-buy",
                  pass: "text-pass",
                  muted: "text-muted",
                }[row.tone ?? "default"],
              )}
            >
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

interface VerdictCopy {
  title: string;
  tone: string;
  meaning: string;
}

const UNSUPPORTED: VerdictCopy = {
  title: "Not enough history to say",
  tone: "text-muted",
  meaning:
    "There is not enough price history to judge whether this gap is normal. That is an absence of evidence, not a clean bill of health.",
};

const VERDICT_COPY: Record<string, VerdictCopy> = {
  structural: {
    title: "The gap has been there",
    tone: "text-buy",
    meaning:
      "Today's gap is in line with how these two markets have priced this item. Buying it is a bet that they keep doing what they have been doing.",
  },
  temporary: {
    title: "The gap is recent",
    tone: "text-review",
    meaning:
      "The gap is not the normal state of these two markets. It may still be worth buying, but it is a bet on a recent move lasting long enough to sell into.",
  },
  unstable: {
    title: "The gap swings",
    tone: "text-risk-high",
    meaning:
      "The gap exists on average, but the prices behind it move enough that what you get on the day you sell is a wide range rather than a number.",
  },
  unsupported: UNSUPPORTED,
};

/** Whether today's spread is a feature of the market or today's accident. */
export function SpreadEvidencePanel({ evidence }: { evidence: SpreadEvidence }) {
  const copy = VERDICT_COPY[evidence.verdict] ?? UNSUPPORTED;
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface shadow-card">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-hairline bg-hairline-top px-5 py-3.5">
        <div>
          <div className="label">How solid is this gap</div>
          <h2
            className={clsx(
              "display mt-1 text-[1.125rem] font-medium tracking-tight",
              copy.tone,
            )}
          >
            {copy.title}
          </h2>
        </div>
        <ConfidenceBadge value={evidence.confidence} label="evidence" />
      </header>
      <div className="px-5 py-4">
        <p className="max-w-2xl text-[0.8125rem] leading-relaxed text-secondary">
          {copy.meaning}
        </p>

        <div className="mt-4 grid gap-px overflow-hidden rounded border border-border bg-border sm:grid-cols-3">
          <div className="bg-surface px-4 py-3">
            <div className="label">Gap today</div>
            <div className="numeric mt-1 text-[0.9375rem] text-primary">
              {money(evidence.current_spread)}
            </div>
          </div>
          <div className="bg-surface px-4 py-3">
            <div className="label">Usual gap</div>
            <div className="numeric mt-1 text-[0.9375rem] text-primary">
              <Value>{money(evidence.typical_spread)}</Value>
            </div>
          </div>
          <div className="bg-surface px-4 py-3">
            <div className="label">Today vs usual</div>
            <div className="numeric mt-1 text-[0.9375rem] text-primary">
              <Value>
                {evidence.spread_ratio === null
                  ? null
                  : `${Number(evidence.spread_ratio).toFixed(2)}x`}
              </Value>
            </div>
          </div>
        </div>

        <ul className="mt-4 space-y-1.5">
          {evidence.reasons.map((reason) => (
            <li key={reason} className="flex gap-2.5 text-xs leading-relaxed text-muted">
              <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-faint" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {[evidence.source, evidence.target].map((side) => (
            <div key={side.label} className="text-2xs text-faint">
              <div className="label">
                {side.label === "source" ? "Buying side history" : "Selling side history"}
              </div>
              <div className="mt-1 space-y-0.5">
                <div>
                  Median <Value>{money(side.median)}</Value> over{" "}
                  {side.window_days ? `${side.window_days} days` : "no usable window"}
                </div>
                <div>
                  {side.observation_count} observation(s), price varies{" "}
                  <Value>{percent(side.volatility, 0)}</Value>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** The score, reproducible by hand from the rows shown. */
export function ScoreTable({ breakdown }: { breakdown: ScoreBreakdown }) {
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[0.8125rem]">
          <thead>
            <tr>
              <th className="border-b border-border px-3 py-2 text-left text-3xs uppercase tracking-label text-faint">
                Component
              </th>
              <th className="border-b border-border px-3 py-2 text-left text-3xs uppercase tracking-label text-faint">
                Measured
              </th>
              <th className="border-b border-border px-3 py-2 text-left text-3xs uppercase tracking-label text-faint">
                How it was scored
              </th>
              <th className="border-b border-border px-3 py-2 text-right text-3xs uppercase tracking-label text-faint">
                Score
              </th>
              <th className="border-b border-border px-3 py-2 text-right text-3xs uppercase tracking-label text-faint">
                Weight
              </th>
              <th className="border-b border-border px-3 py-2 text-right text-3xs uppercase tracking-label text-faint">
                Adds
              </th>
            </tr>
          </thead>
          <tbody>
            {breakdown.components.map((component) => (
              <tr key={component.name} className="align-top">
                <td className="border-b border-hairline px-3 py-2.5">
                  <div className="text-secondary">{component.label}</div>
                  {!component.has_data && (
                    <div className="mt-0.5 text-2xs text-review">no data behind this</div>
                  )}
                </td>
                <td className="border-b border-hairline px-3 py-2.5">
                  <dl className="space-y-0.5 text-2xs text-muted">
                    {Object.entries(component.inputs).map(([key, value]) => (
                      <div key={key} className="flex gap-1.5">
                        <dt className="text-faint">{key}:</dt>
                        <dd className="numeric">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </td>
                <td className="border-b border-hairline px-3 py-2.5 text-2xs leading-relaxed text-muted">
                  {component.calculation}
                </td>
                <td className="numeric border-b border-hairline px-3 py-2.5 text-right text-secondary">
                  {formatScore(component.score)}
                </td>
                <td className="numeric border-b border-hairline px-3 py-2.5 text-right text-faint">
                  {percent(component.weight, 0)}
                </td>
                <td className="numeric border-b border-hairline px-3 py-2.5 text-right text-primary">
                  {Number(component.contribution).toFixed(2)}
                </td>
              </tr>
            ))}
            <tr>
              <td className="px-3 py-2.5 font-medium text-primary" colSpan={4}>
                Total
              </td>
              <td className="numeric px-3 py-2.5 text-right text-faint">
                {percent(breakdown.weight_total, 0)}
              </td>
              <td className="numeric px-3 py-2.5 text-right font-medium text-primary">
                {Number(breakdown.contribution_total).toFixed(2)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {breakdown.gated_reason && (
        <div className="rounded border border-pass/30 bg-pass/10 px-3 py-2 text-xs leading-relaxed text-pass">
          Score forced to zero. {breakdown.gated_reason} The model produced{" "}
          {formatScore(breakdown.raw_total)} before this was applied, kept on the record
          so the gate is visible rather than the number just being low.
        </div>
      )}
      <p className="text-2xs leading-relaxed text-faint">
        Model {breakdown.model_version}. Every row is the arithmetic that ran, not a
        description of it: the Adds column sums to the total.
      </p>
    </div>
  );
}

/** Risk, kept apart by kind, because the kinds fail and are fixed differently. */
export function RiskCategories({ risk }: { risk: RiskAssessment }) {
  const categories: RiskCategoryAssessment[] = risk.categories ?? [];
  if (categories.length === 0) {
    return (
      <p className="text-[0.8125rem] text-muted">
        This record was analysed before the risk breakdown existed. Re-analyse it to
        produce one.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        {categories.map((category) => (
          <div
            key={category.category}
            className={clsx(
              "rounded border px-4 py-3",
              category.has_evidence ? "border-border" : "border-dashed border-border",
            )}
          >
            <div className="flex items-baseline justify-between gap-3">
              <div className="text-[0.8125rem] text-primary">{category.label}</div>
              {category.has_evidence ? (
                <RiskBadge value={category.level} />
              ) : (
                <span className="text-2xs text-faint">not assessed</span>
              )}
            </div>
            <div className="mt-2">
              <ScoreBar
                value={Number(category.score)}
                tone={
                  category.level === "low"
                    ? "buy"
                    : category.level === "medium"
                      ? "review"
                      : "pass"
                }
              />
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-muted">{category.summary}</p>
            {category.signals.length > 1 && (
              <p className="mt-1 text-2xs text-faint">
                and {category.signals.length - 1} more signal(s) in this category
              </p>
            )}
          </div>
        ))}
      </div>
      {risk.unassessed_categories?.length > 0 && (
        <p className="text-2xs leading-relaxed text-faint">
          {risk.unassessed_categories.length} category(ies) could not be assessed at all.
          A category with no evidence scores zero for the same arithmetic reason as one
          that was checked and found clean, which is why they are marked differently
          here.
        </p>
      )}
    </div>
  );
}
