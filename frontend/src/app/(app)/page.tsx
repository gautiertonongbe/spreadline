import Link from "next/link";

import {
  Card,
  ConfidenceBadge,
  EmptyState,
  ErrorState,
  Note,
  PageHeader,
  Stat,
  Value,
} from "@/components/ui";
import { endpoints, type DashboardResponse } from "@/lib/api";
import { money, percent, score } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * What to do next, in one line.
 *
 * A dashboard of counters tells an operator how the system is doing. It does
 * not tell them what to do with the next hour, and that is the question they
 * actually opened the tab with. This band answers it from the same stored
 * numbers the counters below are built from, and links straight into the
 * filtered queue rather than leaving the reader to assemble the filter.
 */
function Briefing({
  buy,
  review,
  expectedProfit,
  capitalRequired,
}: {
  buy: number;
  review: number;
  expectedProfit: string;
  capitalRequired: string;
}) {
  if (buy === 0 && review === 0) {
    return (
      <section className="rounded-xl border border-border bg-surface px-6 py-5 shadow-card">
        <div className="label">Now</div>
        <p className="mt-2 max-w-2xl text-[0.9375rem] leading-relaxed text-secondary">
          Nothing is waiting on a decision. Analyse a product to add one.
        </p>
        <Link
          href="/analyze"
          className="mt-3 inline-block text-xs text-accent hover:underline"
        >
          Analyse a product
        </Link>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-border bg-surface shadow-card">
      <div className="px-6 pb-5 pt-5">
        <div className="label">Now</div>
        <p className="display mt-2 max-w-3xl text-[1.375rem] font-medium leading-snug tracking-tight text-primary">
          {buy > 0
            ? `${buy} candidate${buy === 1 ? "" : "s"} clear every threshold, for ${money(
                expectedProfit,
              )} of expected profit on ${money(capitalRequired)} of capital.`
            : `Nothing clears every threshold. ${review} candidate${
                review === 1 ? " is" : "s are"
              } held for a human.`}
        </p>
        {buy > 0 && review > 0 && (
          <p className="mt-2 text-[0.8125rem] text-muted">
            {review} more {review === 1 ? "is" : "are"} held for a human, each with the
            specific requirement it missed.
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-hairline px-6 py-3">
        {buy > 0 && (
          <Link
            href="/opportunities?recommendation=buy"
            className="text-xs text-accent hover:underline"
          >
            Review the {buy} that cleared
          </Link>
        )}
        {review > 0 && (
          <Link
            href="/opportunities?recommendation=review"
            className="text-xs text-accent hover:underline"
          >
            Open the review queue
          </Link>
        )}
        <Link href="/capital" className="text-xs text-muted hover:text-primary">
          Allocate capital across them
        </Link>
      </div>
    </section>
  );
}

export default async function DashboardPage() {
  let data: DashboardResponse;
  try {
    data = await endpoints.dashboard();
  } catch (error) {
    return (
      <ErrorState
        message={`Could not reach the API. ${(error as Error).message}. Start the backend with "make dev" or check NEXT_PUBLIC_API_URL.`}
      />
    );
  }

  const { opportunities, portfolio, prediction_accuracy, validation } = data;
  const buy = opportunities.by_recommendation.buy ?? 0;
  const review = opportunities.by_recommendation.review ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Where inventory capital should go, and how well the platform has predicted that so far."
      />

      <Briefing
        buy={buy}
        review={review}
        expectedProfit={opportunities.buy_expected_profit}
        capitalRequired={opportunities.buy_capital_required}
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Opportunities"
          value={opportunities.total}
          hint={`Average score ${score(opportunities.average_score)}`}
        />
        <Stat label="Buy" value={buy} tone="buy" hint="Every threshold met" />
        <Stat label="Review" value={review} tone="review" hint="Held for a human" />
        <Stat
          label="Expected profit"
          value={money(opportunities.buy_expected_profit)}
          hint={`On ${money(opportunities.buy_capital_required)} of capital, per unit`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Capital" subtitle="Deployed against realised results">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Deployed" value={money(portfolio.capital_deployed)} />
            <Stat label="Open positions" value={portfolio.open_positions} />
            <Stat
              label="Actual profit"
              value={money(portfolio.actual_profit)}
              tone={Number(portfolio.actual_profit) >= 0 ? "buy" : "pass"}
            />
            <Stat
              label="Actual ROI"
              value={<Value>{percent(portfolio.actual_roi)}</Value>}
              hint={
                portfolio.closed_positions
                  ? `${portfolio.closed_positions} closed position(s)`
                  : "No closed positions yet"
              }
            />
          </div>
        </Card>

        <Card
          title="Prediction accuracy"
          subtitle="The platform's report card on itself"
        >
          {prediction_accuracy.sample_size === 0 ? (
            <EmptyState
              title="Nothing to measure yet"
              description={
                prediction_accuracy.caveat ??
                "Record a purchase and its sale, and the predicted economics will be compared against what actually happened."
              }
            />
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Stat
                  label="Predicted"
                  value={money(prediction_accuracy.predicted_profit_total)}
                />
                <Stat
                  label="Actual"
                  value={money(prediction_accuracy.actual_profit_total)}
                />
                <Stat
                  label="Variance"
                  value={money(prediction_accuracy.total_variance)}
                  tone={Number(prediction_accuracy.total_variance ?? 0) >= 0 ? "buy" : "pass"}
                />
                <Stat
                  label="Mean error"
                  value={<Value>{percent(prediction_accuracy.mean_absolute_pct_error)}</Value>}
                  hint={`${prediction_accuracy.sample_size} closed position(s)`}
                />
              </div>
              {prediction_accuracy.caveat && (
                <Note tone="warning">{prediction_accuracy.caveat}</Note>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Hand validation"
        subtitle="Products personally checked before the automation is trusted at scale"
        actions={
          <Link
            href="/opportunities"
            className="text-2xs uppercase tracking-label text-faint transition hover:text-accent"
          >
            Validate an opportunity
          </Link>
        }
      >
        <div className="space-y-3">
          <div className="flex items-baseline justify-between">
            <div className="display text-3xl font-medium text-primary">
              {validation.validated_count}
              <span className="text-lg font-normal text-faint">
                {" "}
                / {validation.target_count}
              </span>
            </div>
            <ConfidenceBadge
              value={validation.is_statistically_meaningful ? "high" : "low"}
              label="statistical weight"
            />
          </div>
          <div className="h-[3px] w-full overflow-hidden rounded-full bg-overlay">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Number(validation.progress_pct) * 100}%` }}
            />
          </div>
          <div className="grid grid-cols-3 gap-3 pt-1">
            <div>
              <div className="label">
                Match accuracy
              </div>
              <div className="numeric mt-1 text-sm text-secondary">
                <Value>{percent(validation.match_accuracy.rate)}</Value>
                <span className="ml-1.5 text-2xs text-faint">
                  ({validation.match_accuracy.checked} checked)
                </span>
              </div>
            </div>
            <div>
              <div className="label">
                Buy precision
              </div>
              <div className="numeric mt-1 text-sm text-secondary">
                <Value>{percent(validation.buy_precision.rate)}</Value>
                <span className="ml-1.5 text-2xs text-faint">
                  ({validation.buy_precision.checked} checked)
                </span>
              </div>
            </div>
            <div>
              <div className="label">
                False positives
              </div>
              <div className="numeric mt-1 text-sm text-secondary">
                <Value>{percent(validation.false_positive_rate.rate)}</Value>
                <span className="ml-1.5 text-2xs text-faint">
                  ({validation.false_positive_rate.checked} checked)
                </span>
              </div>
            </div>
          </div>
          {validation.caveat && <Note>{validation.caveat}</Note>}
        </div>
      </Card>
    </div>
  );
}
