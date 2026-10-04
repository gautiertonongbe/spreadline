import Link from "next/link";

import { RunDecision } from "@/components/autonomy-controls";
import { DecisionActions } from "@/components/decision-actions";
import {
  Badge,
  ConfidenceBadge,
  Disclosure,
  ErrorState,
  MarketStrip,
  type MarketSideView,
  RiskBadge,
  Stat,
  Table,
  Td,
  Th,
  Value,
  Verdict,
} from "@/components/ui";
import {
  api,
  type Economics,
  type Gate,
  type PriceHistory,
  type CompetitionAssessment,
  type DemandAssessment,
  type Provenance,
  type RiskAssessment,
  type ScoreBreakdown,
  type SpreadEvidence,
} from "@/lib/api";
import {
  RiskCategories,
  ScoreTable,
  SpreadEvidencePanel,
  SpreadLadder,
} from "@/components/evidence";
import {
  date,
  money,
  percent,
  relativeDate,
  score,
  signedMoney,
  titleCase,
} from "@/lib/format";

export const dynamic = "force-dynamic";

interface Detail {
  id: string;
  status: string;
  recommendation: string;
  score: string | null;
  risk_level: string;
  risk_score: string | null;
  match_confidence: string | null;
  data_quality_score: string | null;
  demand_confidence: string;
  source_marketplace: string;
  target_marketplace: string;
  analyzed_at: string | null;
  score_components: Partial<ScoreBreakdown>;
  explanation: {
    headline?: string;
    reasons?: string[];
    risks?: string[];
    gates?: Gate[];
    warnings?: string[];
    is_live_data?: boolean;
  };
  product: { id: string; title: string; brand: string | null; category: string | null; primary_gtin: string | null } | null;
  source_listing: Listing | null;
  target_listing: Listing | null;
  match: {
    confidence: string;
    method: string;
    status: string;
    evidence: { type: string; detail: string }[];
    conflicts: { dimension: string; message: string; blocking: boolean }[];
    variation_check_passed: boolean | null;
  } | null;
  economics: Economics | null;
  risk: RiskAssessment | null;
  stress_test: { scenarios: Economics[] } | null;
  price_history: { source: PriceHistory | null; target: PriceHistory | null } | null;
  spread_evidence: SpreadEvidence | null;
  demand: DemandAssessment | null;
  competition: CompetitionAssessment | null;
  provenance: Provenance[];
  events: {
    id: string;
    type: string;
    from_status: string | null;
    to_status: string | null;
    actor: string;
    message: string | null;
    created_at: string | null;
  }[];
  validations: Record<string, unknown>[];
}

interface Listing {
  id: string;
  marketplace: string;
  external_id: string;
  title: string;
  url: string | null;
  current_price: string | null;
  availability: string;
  seller_count: number | null;
  sales_rank: number | null;
  review_count: number | null;
}

async function fetchDetail(id: string): Promise<Detail> {
  // Through the shared client rather than a bare fetch: that is the one place
  // that forwards the caller's session, and a page that reached past it looked
  // signed out no matter who was reading it.
  return api.get<Detail>(`/opportunities/${id}`);
}

/** Map a stored listing onto the shared two-market strip. */
function asSide(listing: Listing | null): MarketSideView | null {
  if (!listing) return null;
  return {
    marketplace: titleCase(listing.marketplace),
    price: listing.current_price,
    title: listing.title,
    externalId: listing.external_id,
    availability: listing.availability,
    sellerCount: listing.seller_count,
    salesRank: listing.sales_rank,
    url: listing.url,
  };
}

export default async function OpportunityDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail: Detail;
  try {
    detail = await fetchDetail(id);
  } catch (error) {
    return <ErrorState message={`Could not load this opportunity. ${(error as Error).message}`} />;
  }

  const components = detail.score_components?.components ?? [];
  const gates = detail.explanation?.gates ?? [];
  const reasons = detail.explanation?.reasons ?? [];
  const economics = detail.economics;
  const gated = detail.score_components?.gated_reason;
  const signals = detail.risk?.signals ?? [];

  // Hard gates first: a hard gate ends the discussion, a soft gate only says why
  // a profitable candidate is being held for a human.
  const failedGates = gates
    .filter((gate) => !gate.passed)
    .sort((a, b) => Number(b.hard) - Number(a.hard));
  const blockers = [
    ...(gated ? [{ text: `Score withheld. ${gated}`, hard: true }] : []),
    ...failedGates.map((gate) => ({ text: `${gate.label}. ${gate.detail}`, hard: gate.hard })),
    ...signals
      .filter((signal) => signal.blocking)
      .map((signal) => ({ text: signal.message, hard: true })),
  ];

  const passedGateCount = gates.length - failedGates.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link href="/opportunities" className="text-2xs text-faint transition hover:text-accent">
            Opportunities
          </Link>
          <h1 className="display mt-1.5 truncate text-[1.625rem] font-medium leading-tight tracking-tight text-primary">
            {detail.product?.title ?? detail.source_listing?.title ?? "Opportunity"}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2.5 text-2xs text-faint">
            {detail.product?.brand && <span>{detail.product.brand}</span>}
            <RiskBadge value={detail.risk_level} />
            <Badge>{titleCase(detail.status)}</Badge>
            <span>Score {score(detail.score)}</span>
            <span>Data quality {score(detail.data_quality_score)}</span>
            {detail.explanation?.is_live_data === false && (
              <Badge tone="warning">Fixture data, not a market observation</Badge>
            )}
          </div>
        </div>
        <DecisionActions opportunityId={detail.id} status={detail.status} />
      </div>

      <Verdict
        recommendation={detail.recommendation}
        headline={detail.explanation?.headline}
        blockers={blockers}
        numbers={[
          {
            label: "You make",
            value: money(economics?.net_profit),
            tone: Number(economics?.net_profit ?? 0) >= 0 ? "buy" : "pass",
            hint: `On each one, after every fee. You pay ${money(
              economics?.acquisition_cost,
            )} and ${money(economics?.total_fees)} goes in fees.`,
          },
          {
            label: "Back on your money",
            value: <Value>{percent(economics?.roi ?? null)}</Value>,
            hint: `You keep ${percent(economics?.margin ?? null)} of the ${money(
              economics?.sale_price,
            )} it sells for.`,
          },
          {
            label: "Do not pay over",
            value: <Value>{money(economics?.max_acquisition_cost)}</Value>,
            hint: `Pay more than this and you stop making money, even at the full ${money(
              economics?.sale_price,
            )} sale price.`,
          },
        ]}
      />

      <MarketStrip
        source={asSide(detail.source_listing)}
        target={asSide(detail.target_listing)}
        matchConfidence={detail.match_confidence}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        {economics && (
          <SpreadLadder
            economics={economics}
            sourceMarketplace={detail.source_marketplace}
            targetMarketplace={detail.target_marketplace}
          />
        )}
        {detail.spread_evidence && (
          <SpreadEvidencePanel evidence={detail.spread_evidence} />
        )}
      </div>

      <div className="pt-2">
        <div className="mb-3 flex items-center gap-3">
          <span className="label">The working</span>
          <span className="rule-fade flex-1" />
        </div>

        <div className="space-y-2.5">
          <Disclosure
            title="Why this call"
            summary={`${reasons.length} supporting, ${signals.length} risk signal(s), ${passedGateCount} of ${gates.length} gates passed`}
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div className="space-y-4">
                {reasons.length > 0 && (
                  <div>
                    <div className="label">Supporting</div>
                    <ul className="mt-2 space-y-1.5 text-[0.8125rem] leading-relaxed">
                      {reasons.map((reason) => (
                        <li key={reason} className="flex gap-2.5">
                          <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-buy" />
                          <span className="text-secondary">{reason}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {signals.length > 0 && (
                  <div>
                    <div className="label">Risk signals</div>
                    <ul className="mt-2 space-y-1.5 text-[0.8125rem] leading-relaxed">
                      {signals.map((signal) => (
                        <li key={signal.code} className="flex gap-2.5">
                          <span
                            className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${
                              signal.blocking ? "bg-pass" : "bg-review"
                            }`}
                          />
                          <span className="text-secondary">{signal.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <div>
                <div className="label">Decision gates</div>
                <div className="mt-2 space-y-2">
                  {gates.map((gate) => (
                    <div key={gate.code} className="flex items-start gap-2.5 text-[0.8125rem]">
                      <span
                        className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${
                          gate.passed ? "bg-buy" : gate.hard ? "bg-pass" : "bg-review"
                        }`}
                      />
                      <div className="min-w-0">
                        <div className="text-secondary">{gate.label}</div>
                        <div className="text-2xs text-muted">{gate.detail}</div>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-3 text-2xs leading-relaxed text-faint">
                  A hard gate forces a pass. A soft gate only blocks a buy, holding the
                  candidate for a human.
                </p>
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Risk, by kind"
            summary={
              detail.risk?.categories
                ? `${detail.risk.categories.filter((item) => item.has_evidence).length} of ${detail.risk.categories.length} assessed` +
                  (detail.risk.driving_category ? `, led by ${detail.risk.driving_category.replace(/_/g, " ")}` : "")
                : "no breakdown stored"
            }
          >
            {detail.risk ? (
              <div className="space-y-4">
                <p className="text-[0.8125rem] leading-relaxed text-secondary">
                  {detail.risk.summary}
                </p>
                <RiskCategories risk={detail.risk} />
              </div>
            ) : (
              <p className="text-[0.8125rem] text-muted">No risk assessment stored.</p>
            )}
          </Disclosure>

          <Disclosure
            title="The money"
            summary={
              economics
                ? `${money(economics.sale_price)} sale, ${money(economics.total_fees)} fees, ${money(
                    economics.net_profit,
                  )} net`
                : "no snapshot stored"
            }
          >
            {economics ? (
              <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
                <Table>
                  <thead>
                    <tr>
                      <Th>Line item</Th>
                      <Th>Basis</Th>
                      <Th align="right">Amount</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {economics.line_items.map((item) => (
                      <tr key={item.code}>
                        <Td>{item.label}</Td>
                        <Td className="text-2xs text-muted">{item.basis}</Td>
                        <Td
                          align="right"
                          numeric
                          className={item.sign > 0 ? "text-primary" : "text-muted"}
                        >
                          {item.sign > 0 ? "" : "-"}
                          {money(item.amount)}
                        </Td>
                      </tr>
                    ))}
                    <tr>
                      <Td className="font-semibold">Net profit</Td>
                      <Td />
                      <Td
                        align="right"
                        numeric
                        className={`font-semibold ${
                          Number(economics.net_profit) >= 0 ? "text-buy" : "text-pass"
                        }`}
                      >
                        {money(economics.net_profit)}
                      </Td>
                    </tr>
                  </tbody>
                </Table>
                <div className="space-y-3">
                  <Stat label="Break-even sale price" value={money(economics.breakeven_sale_price)} />
                  <Stat
                    label="Maximum acquisition cost"
                    value={money(economics.max_acquisition_cost)}
                    hint="At break-even. The most you can pay per unit."
                  />
                  <Stat label="Total fees" value={money(economics.total_fees)} />
                  <div className="text-2xs text-faint">
                    Fee assumptions {economics.assumptions_version ?? "unknown"}, frozen into
                    this snapshot.
                  </div>
                  {economics.warnings && economics.warnings.length > 0 && (
                    <div className="rounded border border-review/30 bg-review/10 px-3 py-2 text-2xs text-review">
                      {economics.warnings.map((warning) => (
                        <div key={warning}>{warning}</div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-[0.8125rem] text-muted">No profitability snapshot stored.</p>
            )}
          </Disclosure>

          <Disclosure
            title="Product identity"
            summary={
              detail.match
                ? `${percent(detail.match.confidence, 0)} via ${titleCase(detail.match.method)}, ${
                    detail.match.conflicts.length
                  } conflict(s)`
                : "no stored match record"
            }
          >
            {detail.match ? (
              <div className="grid gap-6 lg:grid-cols-2">
                <div>
                  <div className="flex items-center justify-between">
                    <div className="numeric text-2xl text-primary">
                      {percent(detail.match.confidence, 0)}
                    </div>
                    <div className="text-right">
                      <Badge tone={detail.match.status === "confirmed" ? "success" : "warning"}>
                        {titleCase(detail.match.status)}
                      </Badge>
                      <div className="mt-1 text-2xs text-muted">
                        via {titleCase(detail.match.method)}
                      </div>
                    </div>
                  </div>
                  <p className="mt-3 text-2xs leading-relaxed text-faint">
                    Title similarity alone can never reach high confidence. Confidence at this
                    level came from the evidence listed here.
                  </p>
                </div>
                <div className="space-y-3">
                  <div>
                    <div className="label">Evidence</div>
                    <ul className="mt-1.5 space-y-1 text-[0.8125rem] text-muted">
                      {detail.match.evidence.map((item, index) => (
                        <li key={`${item.type}-${index}`}>{item.detail}</li>
                      ))}
                    </ul>
                  </div>
                  {detail.match.conflicts.length > 0 && (
                    <div>
                      <div className="label text-pass">Conflicts</div>
                      <ul className="mt-1.5 space-y-1 text-[0.8125rem] text-pass">
                        {detail.match.conflicts.map((conflict, index) => (
                          <li key={`${conflict.dimension}-${index}`}>{conflict.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="text-2xs text-faint">
                    Variation check:{" "}
                    {detail.match.variation_check_passed ? "passed" : "conflicts present"}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-[0.8125rem] text-muted">No stored match record.</p>
            )}
          </Disclosure>

          <Disclosure
            title="Demand and competition"
            summary={
              detail.demand
                ? `Demand ${detail.demand.confidence}, ${
                    detail.competition?.seller_count ?? "unknown"
                  } sellers`
                : "not stored"
            }
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <div className="flex items-center justify-between">
                  <span className="label">Does it sell</span>
                  {detail.demand && <ConfidenceBadge value={detail.demand.confidence} />}
                </div>
                <div className="numeric mt-1.5 text-lg text-primary">
                  <Value>{score(detail.demand?.score ?? null)}</Value>
                </div>
                <ul className="mt-1.5 space-y-0.5 text-2xs text-muted">
                  {(detail.demand?.reasons ?? []).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <span className="label">Who else is selling it</span>
                  {detail.competition && <RiskBadge value={detail.competition.risk_level} />}
                </div>
                <div className="numeric mt-1.5 text-lg text-primary">
                  <Value>
                    {detail.competition?.seller_count === null ||
                    detail.competition?.seller_count === undefined
                      ? null
                      : `${detail.competition.seller_count} sellers`}
                  </Value>
                </div>
                <ul className="mt-1.5 space-y-0.5 text-2xs text-muted">
                  {(detail.competition?.reasons ?? []).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Score breakdown"
            summary={`${components.length} weighted components, every calculation shown`}
          >
            {detail.score_components?.components ? (
              <ScoreTable breakdown={detail.score_components as ScoreBreakdown} />
            ) : (
              <p className="text-[0.8125rem] text-muted">No score breakdown stored.</p>
            )}
          </Disclosure>

          {detail.stress_test?.scenarios && detail.stress_test.scenarios.length > 0 && (
            <Disclosure
              title="Stress test"
              summary={`${detail.stress_test.scenarios.length} scenarios against the base case`}
            >
              <Table>
                <thead>
                  <tr>
                    <Th>Scenario</Th>
                    <Th align="right">Sale price</Th>
                    <Th align="right">Acquisition</Th>
                    <Th align="right">Fees</Th>
                    <Th align="right">Profit</Th>
                    <Th align="right">ROI</Th>
                    <Th align="right">Margin</Th>
                  </tr>
                </thead>
                <tbody>
                  {detail.stress_test.scenarios.map((scenario, index) => {
                    const row = scenario as unknown as {
                      scenario: string;
                      sale_price: string;
                      acquisition_cost: string;
                      total_fees: string;
                      net_profit: string;
                      roi: string | null;
                      margin: string | null;
                    };
                    return (
                      <tr key={`${row.scenario}-${index}`}>
                        <Td>{titleCase(row.scenario)}</Td>
                        <Td align="right" numeric>
                          {money(row.sale_price)}
                        </Td>
                        <Td align="right" numeric>
                          {money(row.acquisition_cost)}
                        </Td>
                        <Td align="right" numeric className="text-muted">
                          {money(row.total_fees)}
                        </Td>
                        <Td
                          align="right"
                          numeric
                          className={Number(row.net_profit) > 0 ? "text-buy" : "text-pass"}
                        >
                          {signedMoney(row.net_profit)}
                        </Td>
                        <Td align="right" numeric>
                          <Value>{percent(row.roi)}</Value>
                        </Td>
                        <Td align="right" numeric>
                          <Value>{percent(row.margin)}</Value>
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </Disclosure>
          )}

          <Disclosure
            title="Price history"
            summary={
              detail.price_history?.target
                ? `${detail.price_history.target.observation_count} observations on the exit market`
                : "no stored observations"
            }
          >
            <div className="grid gap-6 lg:grid-cols-2">
              {(["source", "target"] as const).map((side) => {
                const history = detail.price_history?.[side];
                if (!history) return null;
                return (
                  <div key={side}>
                    <div className="flex items-center justify-between">
                      <div className="label">{side} market</div>
                      <ConfidenceBadge value={history.confidence} />
                    </div>
                    <div className="mt-1 text-2xs text-muted">
                      {history.observation_count} observations over {history.history_span_days}{" "}
                      days
                      {history.reference_window
                        ? `, reference window ${history.reference_window} days`
                        : ", no window qualifies as a baseline"}
                    </div>
                    <Table>
                      <thead>
                        <tr>
                          <Th>Window</Th>
                          <Th align="right">Median</Th>
                          <Th align="right">Min</Th>
                          <Th align="right">Max</Th>
                          <Th align="right">Volatility</Th>
                          <Th>Status</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.values(history.windows)
                          .sort((a, b) => a.window_days - b.window_days)
                          .map((window) => (
                            <tr key={window.window_days}>
                              <Td>{window.window_days}d</Td>
                              <Td align="right" numeric>
                                <Value>{money(window.median)}</Value>
                              </Td>
                              <Td align="right" numeric>
                                <Value>{money(window.minimum)}</Value>
                              </Td>
                              <Td align="right" numeric>
                                <Value>{money(window.maximum)}</Value>
                              </Td>
                              <Td align="right" numeric>
                                <Value>{percent(window.volatility, 0)}</Value>
                              </Td>
                              <Td className="text-2xs text-muted">
                                {window.sufficient
                                  ? `${window.observation_count} obs`
                                  : (window.reason ?? "insufficient")}
                              </Td>
                            </tr>
                          ))}
                      </tbody>
                    </Table>
                  </div>
                );
              })}
            </div>
          </Disclosure>

          <Disclosure
            title="Could this be bought autonomously"
            summary="Runs the deterministic eligibility checks against the active policy"
          >
            <div className="space-y-3">
              <p className="text-[0.8125rem] leading-relaxed text-secondary">
                Scoring ranks candidates against each other. Eligibility decides whether
                one may be bought without a person, and every check is mandatory: one
                failure makes the answer a review rather than a buy.
              </p>
              <RunDecision opportunityId={detail.id} />
              <p className="text-2xs leading-relaxed text-faint">
                Running this records a decision either way. A refusal is kept as carefully
                as an authorisation, because the refusals are the evidence the limits
                work. Nothing is ordered: Spreadline decides what, where, how much and at
                what maximum price, and you place the order.
              </p>
              <Link
                href="/autonomy"
                className="inline-block text-xs text-accent hover:underline"
              >
                The autonomy policy and limits
              </Link>
            </div>
          </Disclosure>

          <Disclosure
            title="Where the data came from"
            summary={
              detail.provenance?.length
                ? detail.provenance
                    .map((entry) => entry.provider ?? "unknown")
                    .join(", ")
                : "not recorded"
            }
          >
            <div className="space-y-3">
              {(detail.provenance ?? []).map((entry) => (
                <div key={entry.role} className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                  <span className="label w-24">
                    {entry.role === "source" ? "Buying side" : "Selling side"}
                  </span>
                  <span className="text-[0.8125rem] text-secondary">
                    {titleCase(entry.marketplace)} · {entry.provider ?? "unknown provider"}
                  </span>
                  <span className="text-2xs text-faint">
                    {entry.is_live_data === null
                      ? "provider no longer registered"
                      : entry.is_live_data
                        ? "live market call"
                        : "fixture data, not a market observation"}
                  </span>
                  <span className="text-2xs text-faint">
                    seen {relativeDate(entry.observed_at)}
                  </span>
                </div>
              ))}
              <p className="text-2xs leading-relaxed text-faint">
                Every price above is only as good as what reported it. When a real
                provider is connected the values here change and nothing else does.
              </p>
            </div>
          </Disclosure>

          <Disclosure
            title="Decision history"
            summary={`${detail.events.length} recorded transition(s)`}
          >
            <ol className="space-y-3">
              {detail.events.map((event) => (
                <li key={event.id} className="border-l border-border pl-3">
                  <div className="text-[0.8125rem] text-secondary">
                    {titleCase(event.type)}
                    {event.to_status && (
                      <span className="text-muted">
                        {" "}
                        {event.from_status ? `${event.from_status} to ` : ""}
                        {event.to_status}
                      </span>
                    )}
                  </div>
                  {event.message && <div className="text-2xs text-muted">{event.message}</div>}
                  <div className="mt-0.5 text-2xs text-faint">
                    {date(event.created_at)} by {event.actor}
                  </div>
                </li>
              ))}
            </ol>
          </Disclosure>
        </div>
      </div>
    </div>
  );
}
