import Link from "next/link";

import { Card, ConfidenceBadge, EmptyState, ErrorState, Stat, Value } from "@/components/ui";
import { endpoints, type DashboardResponse } from "@/lib/api";
import { money, percent, score } from "@/lib/format";

export const dynamic = "force-dynamic";

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
      <div>
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <p className="mt-1 text-sm text-muted">
          Where inventory capital should go, and how well the platform has predicted
          that so far.
        </p>
      </div>

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
                <p className="rounded border border-review/30 bg-review/10 px-3 py-2 text-xs text-review">
                  {prediction_accuracy.caveat}
                </p>
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
            className="text-xs text-accent transition hover:underline"
          >
            Validate an opportunity
          </Link>
        }
      >
        <div className="space-y-3">
          <div className="flex items-baseline justify-between">
            <div className="numeric text-2xl font-semibold">
              {validation.validated_count}
              <span className="text-base font-normal text-muted">
                {" "}
                / {validation.target_count}
              </span>
            </div>
            <ConfidenceBadge
              value={validation.is_statistically_meaningful ? "high" : "low"}
              label="statistical weight"
            />
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Number(validation.progress_pct) * 100}%` }}
            />
          </div>
          <div className="grid grid-cols-3 gap-3 pt-1">
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">
                Match accuracy
              </div>
              <div className="numeric mt-0.5 text-sm">
                <Value>{percent(validation.match_accuracy.rate)}</Value>
                <span className="ml-1 text-2xs text-muted">
                  ({validation.match_accuracy.checked} checked)
                </span>
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">
                Buy precision
              </div>
              <div className="numeric mt-0.5 text-sm">
                <Value>{percent(validation.buy_precision.rate)}</Value>
                <span className="ml-1 text-2xs text-muted">
                  ({validation.buy_precision.checked} checked)
                </span>
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">
                False positives
              </div>
              <div className="numeric mt-0.5 text-sm">
                <Value>{percent(validation.false_positive_rate.rate)}</Value>
                <span className="ml-1 text-2xs text-muted">
                  ({validation.false_positive_rate.checked} checked)
                </span>
              </div>
            </div>
          </div>
          {validation.caveat && (
            <p className="rounded border border-border bg-raised px-3 py-2 text-xs text-muted">
              {validation.caveat}
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
