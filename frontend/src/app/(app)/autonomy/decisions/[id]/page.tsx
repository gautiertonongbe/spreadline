import Link from "next/link";

import { OutcomeBadge, VerdictDot, stageLabel } from "@/components/autonomy-ui";
import { Card, Disclosure, ErrorState, Stat, Value } from "@/components/ui";
import { autonomyApi, type DecisionDetail } from "@/lib/api";
import { date, money, percent } from "@/lib/format";

export const dynamic = "force-dynamic";

interface EvidencePolicy {
  version?: string;
  capital_limit?: string;
  minimum_roi?: string;
  maximum_risk?: string;
  minimum_match_confidence?: string;
  autonomy_level?: number;
  execution_mode?: string;
}

interface EvidenceOpportunity {
  id?: string;
  score?: string | null;
  recommendation?: string;
  acquisition_cost?: string | null;
  expected_sale_price?: string | null;
  net_profit?: string | null;
  roi?: string | null;
  risk_level?: string;
  match_confidence?: string | null;
  data_quality_score?: string | null;
  source_marketplace?: string;
  target_marketplace?: string;
}

interface EvidenceEconomics {
  sale_price?: string;
  acquisition_cost?: string;
  total_fees?: string;
  total_cost?: string;
  net_profit?: string;
  max_acquisition_cost?: string | null;
  assumptions_version?: string;
}

interface EvidenceEligibility {
  eligible?: boolean;
  summary?: string;
  checks?: { code: string; label: string; passed: boolean; required: string; actual: string }[];
}

/**
 * One decision, with the evidence exactly as it was when it was made.
 *
 * "Why did Spreadline buy this" has to be answerable from this page alone, six
 * months later, without the surrounding rows still being what they were. That
 * is why everything here is read out of the frozen snapshot rather than
 * re-fetched from the live opportunity.
 */
export default async function DecisionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail: DecisionDetail;
  try {
    detail = await autonomyApi.decision(id);
  } catch (error) {
    return <ErrorState message={`Could not load this decision. ${(error as Error).message}`} />;
  }

  const evidence = detail.evidence as {
    captured_at?: string;
    opportunity?: EvidenceOpportunity;
    economics?: EvidenceEconomics | null;
    policy?: EvidencePolicy;
    eligibility?: EvidenceEligibility;
  };
  const opportunity = evidence.opportunity ?? {};
  const economics = evidence.economics ?? null;
  const policy = evidence.policy ?? {};
  const checks = evidence.eligibility?.checks ?? [];

  return (
    <div className="space-y-4">
      <div className="min-w-0">
        <Link
          href="/autonomy/decisions"
          className="text-2xs text-faint transition hover:text-accent"
        >
          Decisions
        </Link>
        <div className="mt-1.5 flex flex-wrap items-center gap-3">
          <OutcomeBadge outcome={detail.outcome} />
          <h1 className="display truncate text-[1.375rem] font-medium leading-tight tracking-tight text-primary">
            {detail.reason}
          </h1>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-faint">
          {detail.reason_code && <span className="font-mono">{detail.reason_code}</span>}
          <span>
            Policy {detail.policy_version}, level {detail.autonomy_level},{" "}
            {detail.execution_mode} mode
          </span>
          <span>{date(detail.created_at)}</span>
          {detail.opportunity_id && (
            <Link
              href={`/opportunities/${detail.opportunity_id}`}
              className="text-accent hover:underline"
            >
              The opportunity
            </Link>
          )}
        </div>
      </div>

      {detail.outcome === "authorized" && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Units" value={detail.quantity ?? 0} />
          <Stat
            label="Capital committed"
            value={<Value>{detail.capital_committed && money(detail.capital_committed)}</Value>}
          />
          <Stat
            label="Do not pay over"
            value={<Value>{detail.max_unit_price && money(detail.max_unit_price)}</Value>}
            hint="per unit"
          />
          <Stat
            label="Expected profit"
            value={<Value>{detail.expected_profit && money(detail.expected_profit)}</Value>}
            tone="buy"
          />
        </div>
      )}

      <Card
        title="What each stage concluded"
        subtitle="Recorded separately so a disagreement is visible rather than averaged away"
      >
        <div className="space-y-3">
          {detail.runs.map((run) => (
            <div key={`${run.stage}-${run.created_at}`} className="flex items-start gap-3">
              <div className="w-[110px] shrink-0">
                <div className="text-[0.8125rem] text-secondary">{stageLabel(run.stage)}</div>
                <VerdictDot verdict={run.verdict} />
              </div>
              <p className="min-w-0 flex-1 text-2xs leading-relaxed text-muted">
                {run.summary}
              </p>
            </div>
          ))}
        </div>
      </Card>

      <div className="mb-1 mt-4 flex items-center gap-3">
        <span className="label">The evidence, as it was</span>
        <span className="rule-fade flex-1" />
      </div>

      <div className="space-y-2.5">
        <Disclosure
          title="The candidate"
          summary={`${opportunity.recommendation ?? "unknown"}, score ${opportunity.score ?? "n/a"}`}
        >
          <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
            {[
              ["Recommendation", opportunity.recommendation],
              ["Score", opportunity.score],
              ["Buy on", opportunity.source_marketplace],
              ["Sell on", opportunity.target_marketplace],
              ["Cost per item", opportunity.acquisition_cost && money(opportunity.acquisition_cost)],
              [
                "Sells for",
                opportunity.expected_sale_price && money(opportunity.expected_sale_price),
              ],
              ["Profit per item", opportunity.net_profit && money(opportunity.net_profit)],
              ["Return", opportunity.roi && percent(opportunity.roi)],
              ["Risk", opportunity.risk_level],
              [
                "Same item confidence",
                opportunity.match_confidence && percent(opportunity.match_confidence, 0),
              ],
              ["Data quality", opportunity.data_quality_score],
            ].map(([label, value]) => (
              <div
                key={String(label)}
                className="flex items-baseline justify-between gap-4 border-b border-hairline py-1.5"
              >
                <span className="text-2xs text-faint">{label}</span>
                <span className="numeric text-2xs text-secondary">
                  <Value>{value as string}</Value>
                </span>
              </div>
            ))}
          </div>
        </Disclosure>

        {economics && (
          <Disclosure
            title="The economics it was underwritten on"
            summary={`${money(economics.sale_price ?? null)} sale, ${money(
              economics.total_fees ?? null,
            )} fees`}
          >
            <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
              {[
                ["Sells for", economics.sale_price],
                ["Cost per item", economics.acquisition_cost],
                ["Fees", economics.total_fees],
                ["Total cost", economics.total_cost],
                ["Net profit", economics.net_profit],
                ["Most you should pay", economics.max_acquisition_cost],
              ].map(([label, value]) => (
                <div
                  key={String(label)}
                  className="flex items-baseline justify-between gap-4 border-b border-hairline py-1.5"
                >
                  <span className="text-2xs text-faint">{label}</span>
                  <span className="numeric text-2xs text-secondary">
                    <Value>{value ? money(value as string) : null}</Value>
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3 text-2xs text-faint">
              Fee assumptions {economics.assumptions_version}, frozen into this decision.
              A later fee change cannot rewrite it.
            </p>
          </Disclosure>
        )}

        <Disclosure
          title="The eligibility checks"
          summary={evidence.eligibility?.summary ?? ""}
        >
          <div className="space-y-2">
            {checks.map((check) => (
              <div key={check.code} className="flex items-start gap-2.5">
                <span
                  className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${
                    check.passed ? "bg-buy" : "bg-pass"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="text-[0.8125rem] text-secondary">{check.label}</div>
                  <div className="text-2xs text-faint">
                    needs {check.required}, was {check.actual}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-faint">
            Every check is mandatory. There is no weighting and no majority: one failure
            ends it, and the answer becomes a review rather than a buy.
          </p>
        </Disclosure>

        <Disclosure
          title="The policy it was decided under"
          summary={`${policy.version ?? "unknown"}, level ${policy.autonomy_level ?? "?"}`}
        >
          <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
            {[
              ["Version", policy.version],
              ["Level", policy.autonomy_level],
              ["Mode", policy.execution_mode],
              ["Capital limit", policy.capital_limit && money(policy.capital_limit)],
              ["Minimum return", policy.minimum_roi && percent(policy.minimum_roi)],
              ["Maximum risk", policy.maximum_risk],
              [
                "Minimum match confidence",
                policy.minimum_match_confidence && percent(policy.minimum_match_confidence),
              ],
            ].map(([label, value]) => (
              <div
                key={String(label)}
                className="flex items-baseline justify-between gap-4 border-b border-hairline py-1.5"
              >
                <span className="text-2xs text-faint">{label}</span>
                <span className="numeric text-2xs text-secondary">
                  <Value>{value as string}</Value>
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-faint">
            This is the policy as it was, not as it is now. Changing the policy today
            cannot rewrite what this decision was made under.
          </p>
        </Disclosure>
      </div>
    </div>
  );
}
