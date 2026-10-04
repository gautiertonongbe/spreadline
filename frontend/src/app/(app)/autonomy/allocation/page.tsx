import Link from "next/link";

import { BudgetControl, CommitControl } from "@/components/allocation-controls";
import { Card, EmptyState, ErrorState, Note, PageHeader, Stat, Value } from "@/components/ui";
import { autonomyApi, type AllocationPlan } from "@/lib/api";
import { money, percent, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * What to buy now, decided across every candidate at once.
 *
 * The page a person opens holding a budget rather than holding a product. Every
 * other screen answers "is this one worth buying"; this one answers "which of
 * these, and how many", which is a different question with a different answer,
 * and the difference is the whole reason the page exists.
 *
 * Two things are given as much room as the plan itself: what was left out and
 * why, and how much capital is deliberately not being spent. A plan that only
 * showed what it bought would be a sales pitch.
 */
export default async function AllocationPage({
  searchParams,
}: {
  searchParams: Promise<{ budget?: string }>;
}) {
  const params = await searchParams;
  const budget = params.budget?.trim() || null;

  let plan: AllocationPlan;
  try {
    plan = await autonomyApi.allocation(budget ? `?budget=${encodeURIComponent(budget)}` : "");
  } catch (error) {
    return <ErrorState message={`Could not build a plan. ${(error as Error).message}`} />;
  }

  const { capital } = plan;
  const ineligible = Object.entries(plan.considered.ineligible).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="What to buy now"
        description="Every eligible candidate ranked against the others on profit per dollar, with the portfolio limits applied across the whole slate. Nothing here is bought until it is committed."
        actions={
          <div className="flex items-center gap-3">
            <BudgetControl budget={budget} />
            <Link href="/autonomy" className="text-xs text-accent hover:underline">
              Back to autonomy
            </Link>
          </div>
        }
      />

      <section className="rounded-xl border border-border bg-surface px-6 py-5 shadow-card">
        <div className="label">The plan</div>
        <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-secondary">
          {plan.summary}
        </p>
        <p className="mt-3 text-2xs text-faint">
          Policy {plan.policy_version} · {plan.execution_mode} mode ·{" "}
          {plan.considered.eligible} candidate(s) ranked
        </p>
        <div className="mt-4">
          <CommitControl plan={plan} />
        </div>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Budget" value={money(capital.budget)} hint="under the policy limit" />
        <Stat
          label="Committed by this plan"
          value={money(capital.allocated)}
          tone={Number(capital.allocated) > 0 ? "accent" : "muted"}
        />
        <Stat
          label="Deliberately uncommitted"
          value={money(capital.unallocated)}
          hint="nothing else cleared both the policy and the limits"
        />
        <Stat
          label="Expected profit"
          value={money(capital.expected_profit)}
          tone="buy"
          hint={
            capital.expected_return
              ? `${percent(capital.expected_return)} on what is committed`
              : undefined
          }
        />
      </div>

      {plan.lines.length === 0 ? (
        <EmptyState
          title="Nothing to buy"
          description={
            plan.considered.eligible === 0
              ? "No candidate in the catalogue is eligible for autonomous capital under this policy. That is a statement about the candidates and the limits, not a failure to find anything."
              : "Every eligible candidate was stopped by a limit. The table below says which one, and a limit doing its job is the system working rather than failing."
          }
        />
      ) : (
        <Card
          title="What to buy"
          subtitle={
            plan.ranked_on_time
              ? "Ranked by return per year of capital tied up, best first"
              : "Ranked by profit per dollar of capital, best first"
          }
          flush
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hairline text-2xs text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-normal">#</th>
                  <th className="px-3 py-2.5 font-normal">Product</th>
                  <th className="px-3 py-2.5 text-right font-normal">Buy at</th>
                  <th className="px-3 py-2.5 text-right font-normal">Pay no more than</th>
                  <th className="px-3 py-2.5 text-right font-normal">Units</th>
                  <th className="px-3 py-2.5 text-right font-normal">Capital</th>
                  <th className="px-3 py-2.5 text-right font-normal">Expected</th>
                  <th className="px-3 py-2.5 text-right font-normal">Return</th>
                  <th className="px-3 py-2.5 text-right font-normal">
                    {plan.ranked_on_time ? "Per year" : "Days"}
                  </th>
                  <th className="px-5 py-2.5 font-normal">Size set by</th>
                </tr>
              </thead>
              <tbody>
                {plan.lines.map((line) => (
                  <tr
                    key={line.opportunity_id}
                    className="border-b border-hairline last:border-0"
                  >
                    <td className="numeric px-5 py-2.5 text-faint">{line.rank}</td>
                    <td className="max-w-[240px] truncate px-3 py-2.5">
                      <Link
                        href={`/opportunities/${line.opportunity_id}`}
                        className="text-secondary hover:text-accent hover:underline"
                      >
                        {line.title}
                      </Link>
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-secondary">
                      {money(line.unit_cost)}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-muted">
                      {money(line.max_unit_price)}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-primary">
                      {line.quantity}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-secondary">
                      {money(line.capital)}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-buy">
                      {money(line.expected_profit)}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-secondary">
                      {percent(line.return_per_dollar)}
                    </td>
                    <td
                      className="numeric px-3 py-2.5 text-right text-secondary"
                      title={line.velocity_basis ?? undefined}
                    >
                      {line.annualized_return !== null ? (
                        <>
                          {percent(line.annualized_return, 0)}
                          <span className="ml-1 text-faint">
                            ({line.expected_days}d)
                          </span>
                        </>
                      ) : (
                        <Value>{null}</Value>
                      )}
                    </td>
                    <td className="px-5 py-2.5 text-muted">
                      {line.limited_by.replace(/_/g, " ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {plan.marginal_capital && (
        <Card
          title="What more capital would buy"
          subtitle="The best candidate that only money stopped"
        >
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <span className="display text-[1.125rem] text-primary">
              {plan.marginal_capital.title}
            </span>
            <span className="numeric text-[0.9375rem] text-accent">
              {money(plan.marginal_capital.capital_needed)} more
            </span>
            <span className="numeric text-xs text-secondary">
              at {percent(plan.marginal_capital.return_per_dollar)} return
            </span>
          </div>
          <p className="mt-3 max-w-2xl text-xs leading-relaxed text-muted">
            {plan.marginal_capital.detail} Candidates stopped by an exposure limit are not
            listed here: more money would not buy them, and presenting them as if it would
            is an argument for raising a limit dressed up as arithmetic.
          </p>
        </Card>
      )}

      {plan.concentration.length > 0 && (
        <Card
          title="What the portfolio would hold"
          subtitle="Held plus planned, against the limit for each. Closest to binding first."
        >
          <div className="space-y-2.5">
            {plan.concentration.slice(0, 10).map((entry) => {
              const share = Number(entry.share_of_cap ?? "0");
              return (
                <div key={`${entry.dimension}-${entry.key}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-2xs">
                    <span className="text-secondary">
                      {titleCase(entry.key)}{" "}
                      <span className="text-faint">by {entry.dimension}</span>
                    </span>
                    <span className="numeric text-muted">
                      {money(entry.total)} of {money(entry.cap)} ·{" "}
                      <Value>{percent(entry.share_of_cap, 0)}</Value>
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised">
                    <div
                      className={`h-full rounded-full ${
                        share >= 0.9 ? "bg-pass" : share >= 0.7 ? "bg-review" : "bg-accent"
                      }`}
                      style={{ width: `${Math.min(100, Math.max(2, share * 100))}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {plan.excluded.length > 0 && (
        <Card
          title="Ranked and not funded"
          subtitle="What stopped each one. A plan that only showed what it bought would be a sales pitch."
          flush
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hairline text-2xs text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-normal">Product</th>
                  <th className="px-3 py-2.5 font-normal">What stopped it</th>
                  <th className="px-3 py-2.5 text-right font-normal">Return</th>
                  <th className="px-5 py-2.5 font-normal">Detail</th>
                </tr>
              </thead>
              <tbody>
                {plan.excluded.slice(0, 25).map((item) => (
                  <tr
                    key={`${item.opportunity_id}-${item.reason_code}`}
                    className="border-b border-hairline last:border-0"
                  >
                    <td className="max-w-[240px] truncate px-5 py-2.5 text-secondary">
                      {item.title}
                    </td>
                    <td className="px-3 py-2.5 text-secondary">
                      {item.reason_code.toLowerCase().replace(/_/g, " ")}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-muted">
                      <Value>{percent(item.return_per_dollar)}</Value>
                    </td>
                    <td className="px-5 py-2.5 text-muted">{item.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {ineligible.length > 0 && (
        <Card
          title="Refused before ranking"
          subtitle="Candidates that never reached the plan, counted by cause"
        >
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {ineligible.map(([code, count]) => (
              <div key={code} className="flex items-baseline gap-2 text-2xs">
                <span className="numeric text-[0.9375rem] text-primary">{count}</span>
                <span className="text-muted">{code.toLowerCase().replace(/_/g, " ")}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card
        title="How long capital stays tied up"
        subtitle={
          plan.ranked_on_time
            ? "Measured from closed positions, and it is what puts time in the ranking above"
            : "Not measured yet, which is why the ranking above has no time in it"
        }
      >
        <p className="max-w-3xl text-[0.875rem] leading-relaxed text-secondary">
          {plan.velocity.summary}
        </p>
        {plan.velocity.segments.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {plan.velocity.segments.slice(0, 8).map((entry) => (
              <div
                key={`${entry.dimension}-${entry.segment}`}
                className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-hairline py-1.5 text-2xs last:border-0"
              >
                <span className="text-secondary">
                  {titleCase(entry.segment)}{" "}
                  <span className="text-faint">by {entry.dimension}</span>
                </span>
                <span className="numeric text-muted">
                  {entry.median_days}d median · {entry.fastest_days}–{entry.slowest_days}d ·{" "}
                  {entry.sample} closed
                </span>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 max-w-3xl text-2xs leading-relaxed text-faint">
          {plan.velocity.note}
        </p>
      </Card>

      <Note>{plan.note}</Note>

      <p className="text-2xs leading-relaxed text-faint">{plan.ranking}</p>
    </div>
  );
}
