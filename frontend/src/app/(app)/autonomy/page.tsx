import Link from "next/link";

import {
  AutonomySwitch,
  EmergencyStop,
  ResetBreaker,
} from "@/components/autonomy-controls";
import { GatePanel, LevelBadge, MetricCard, breakerValue } from "@/components/autonomy-ui";
import { Card, ErrorState, Note, PageHeader, Stat, Value } from "@/components/ui";
import { autonomyApi, type AutonomyOverview } from "@/lib/api";
import { money, percent } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * The autonomy dashboard.
 *
 * Ordered by what a person needs to know before they need anything else: what
 * the system is currently allowed to do, what it is holding, whether anything
 * has stopped it, and only then how it has been performing.
 */
export default async function AutonomyPage() {
  let data: AutonomyOverview;
  try {
    data = await autonomyApi.overview();
  } catch (error) {
    return <ErrorState message={`Could not load autonomy. ${(error as Error).message}`} />;
  }

  const { policy, level, capital, portfolio, performance, next_level, circuit_breakers } =
    data;
  const tripped = circuit_breakers.filter(
    (breaker) => breaker.state === "tripped" && breaker.is_enabled,
  );
  const headline = performance.metrics.filter((metric) =>
    ["buy_precision", "realized_roi", "prediction_accuracy", "loss_rate"].includes(
      metric.key,
    ),
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Autonomy"
        description="What Spreadline is allowed to do with capital on its own, what it is holding, and whether it has earned more."
        actions={
          <Link href="/autonomy/policy" className="text-xs text-accent hover:underline">
            Policy and limits
          </Link>
        }
      />

      {policy.emergency_stop_active && (
        <div className="rounded-lg border border-pass/30 bg-pass/[0.07] px-5 py-4">
          <div className="label text-pass">Autonomous activity stopped</div>
          <p className="mt-1.5 text-[0.875rem] leading-relaxed text-secondary">
            {policy.emergency_stop_reason}
          </p>
        </div>
      )}

      {tripped.length > 0 && (
        <div className="rounded-lg border border-review/30 bg-review/[0.07] px-5 py-4">
          <div className="label text-review">Circuit breaker tripped</div>
          <p className="mt-1.5 text-[0.875rem] leading-relaxed text-secondary">
            {tripped.map((breaker) => breaker.tripped_reason ?? breaker.label).join(" ")}
          </p>
          <p className="mt-1 text-2xs text-faint">
            New capital is paused until a person resets it. A breaker that cleared itself
            would hide the thing it caught.
          </p>
        </div>
      )}

      <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-5 px-6 pt-6">
          <div>
            <div className="label">Current authority</div>
            <div className="mt-2">
              <LevelBadge
                level={level.current}
                label={level.label}
                mode={level.execution_mode}
              />
            </div>
            <p className="mt-3 max-w-xl text-[0.8125rem] leading-relaxed text-secondary">
              {level.deploys_capital
                ? policy.execution_mode === "live"
                  ? "Spreadline may commit real capital within the policy. It decides what, where, how much and at what maximum price; you place the order."
                  : "Spreadline records positions against real market data without buying anything. The same measurements are taken as for real capital."
                : "Spreadline analyses and recommends. It opens no positions and commits no capital."}
            </p>
          </div>
          <div className="shrink-0 space-y-3">
            <AutonomySwitch overview={data} />
            <EmergencyStop
              active={policy.emergency_stop_active}
              reason={policy.emergency_stop_reason}
            />
          </div>
        </div>

        <div className="mt-6 grid gap-px border-t border-border bg-border sm:grid-cols-3">
          {[
            { label: "Authorised", value: capital.authorized, hint: "the ceiling on its own capital" },
            { label: "Deployed", value: capital.deployed, hint: "committed to open positions" },
            { label: "Available", value: capital.available, hint: "left to commit" },
          ].map((item) => (
            <div key={item.label} className="bg-surface px-6 py-5">
              <div className="label">{item.label}</div>
              <div className="display mt-2 text-[1.75rem] font-medium leading-none tracking-tight text-primary">
                {money(item.value)}
              </div>
              <div className="mt-2 text-2xs text-muted">{item.hint}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <Card
          title="The portfolio"
          subtitle="Inventory held as capital, not as a stock list"
          actions={
            <Link
              href="/autonomy/positions"
              className="text-2xs uppercase tracking-label text-faint transition hover:text-accent"
            >
              All positions
            </Link>
          }
        >
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Open" value={portfolio.open_positions} />
            <Stat label="Closed" value={portfolio.closed_positions} />
            <Stat
              label="Expected profit"
              value={money(portfolio.expected_profit)}
              hint="on open positions"
            />
            <Stat
              label="Realised profit"
              value={money(portfolio.realized_profit)}
              tone={Number(portfolio.realized_profit) >= 0 ? "buy" : "pass"}
              hint={
                portfolio.realized_roi
                  ? `${percent(portfolio.realized_roi)} on closed capital`
                  : "nothing closed yet"
              }
            />
          </div>
          {portfolio.open_positions > 0 && (
            <p className="mt-3 text-2xs text-faint">
              Oldest position {portfolio.oldest_position_days} day(s), average{" "}
              {portfolio.average_age_days} day(s). The policy allows up to{" "}
              {policy.maximum_inventory_age_days}.
            </p>
          )}
        </Card>

        <Card title="Earning the next level">
          {next_level ? (
            <GatePanel gate={next_level} />
          ) : (
            <p className="text-[0.8125rem] text-muted">
              No higher level is defined in the ladder.
            </p>
          )}
        </Card>
      </div>

      <Card
        title="How it has performed"
        subtitle="Every figure carries the sample it was computed from"
        actions={
          <Link
            href="/autonomy/decisions"
            className="text-2xs uppercase tracking-label text-faint transition hover:text-accent"
          >
            Every decision
          </Link>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {headline.map((metric) => (
            <MetricCard key={metric.key} metric={metric} />
          ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-2xs text-faint">
          <span>{performance.decisions_total} decision(s) recorded</span>
          <span>{performance.decisions_autonomous} authorised</span>
          <span>{performance.human_overrides} human override(s)</span>
          <span>
            a ratio needs {performance.meaningful_sample_threshold} observations before it
            counts as evidence
          </span>
        </div>
      </Card>

      <Card
        title="Capital circuit breakers"
        subtitle="These protect capital from the system, not a provider from being hammered"
      >
        <div className="space-y-2">
          {circuit_breakers.map((breaker) => (
            <div
              key={breaker.id}
              className="flex flex-wrap items-start justify-between gap-3 border-b border-hairline py-2.5 last:border-b-0"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      breaker.state === "tripped" ? "bg-pass" : "bg-buy"
                    }`}
                  />
                  <span className="text-[0.8125rem] text-secondary">{breaker.label}</span>
                </div>
                <div className="mt-0.5 text-2xs text-muted">{breaker.detail}</div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="numeric whitespace-nowrap text-2xs text-faint">
                  {breakerValue(breaker.threshold, breaker.unit)} limit
                </span>
                {breaker.state === "tripped" && (
                  <ResetBreaker id={breaker.id} label={breaker.label} />
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Note>
        Spreadline decides what, where, how much and at what maximum price. Placing the
        retail order stays a human action: there is no automated checkout, no purchasing
        bot and no unauthorised scraping. The authorisation record carries everything an
        authorised supplier integration would need when one exists.
      </Note>
    </div>
  );
}
