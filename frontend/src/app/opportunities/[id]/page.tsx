import Link from "next/link";

import { DecisionActions } from "@/components/decision-actions";
import {
  Badge,
  Card,
  ConfidenceBadge,
  ErrorState,
  RecommendationBadge,
  RiskBadge,
  ScoreBar,
  Stat,
  Table,
  Td,
  Th,
  Value,
} from "@/components/ui";
import { API_URL, type Economics, type Gate, type PriceHistory, type RiskSignal } from "@/lib/api";
import { date, money, percent, score, signedMoney, titleCase } from "@/lib/format";

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
  score_components: {
    total?: string;
    raw_total?: string | null;
    gated_reason?: string | null;
    components?: { name: string; score: string; weight: string; contribution: string; basis: string; has_data: boolean }[];
  };
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
  risk: { level: string; score: string; summary: string; signals: RiskSignal[] } | null;
  stress_test: { scenarios: Economics[] } | null;
  price_history: { source: PriceHistory | null; target: PriceHistory | null } | null;
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
  const response = await fetch(`${API_URL}/opportunities/${id}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as Detail;
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

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link href="/opportunities" className="text-xs text-muted hover:text-primary">
            Opportunities
          </Link>
          <h1 className="mt-1 truncate text-lg font-semibold">
            {detail.product?.title ?? detail.source_listing?.title ?? "Opportunity"}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <RecommendationBadge value={detail.recommendation} />
            <RiskBadge value={detail.risk_level} />
            <Badge>{titleCase(detail.status)}</Badge>
            <Badge tone="neutral">
              {detail.source_marketplace} to {detail.target_marketplace}
            </Badge>
            {detail.explanation?.is_live_data === false && (
              <Badge tone="warning">Fixture data, not a market observation</Badge>
            )}
          </div>
        </div>
        <DecisionActions opportunityId={detail.id} status={detail.status} />
      </div>

      {detail.explanation?.headline && (
        <Card title="Recommendation">
          <p className="text-sm">{detail.explanation.headline}</p>
          {gated && (
            <p className="mt-3 rounded border border-pass/30 bg-pass/10 px-3 py-2 text-xs text-pass">
              Score withheld: {gated}
            </p>
          )}
          {reasons.length > 0 && (
            <div className="mt-4">
              <div className="text-2xs uppercase tracking-wide text-muted">Why</div>
              <ul className="mt-1.5 space-y-1 text-sm">
                {reasons.map((reason) => (
                  <li key={reason} className="flex gap-2">
                    <span className="text-buy">+</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {detail.risk?.signals && detail.risk.signals.length > 0 && (
            <div className="mt-4">
              <div className="text-2xs uppercase tracking-wide text-muted">Risks</div>
              <ul className="mt-1.5 space-y-1 text-sm">
                {detail.risk.signals.map((signal) => (
                  <li key={signal.code} className="flex gap-2">
                    <span className={signal.blocking ? "text-pass" : "text-review"}>
                      {signal.blocking ? "x" : "!"}
                    </span>
                    <span>{signal.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Score" value={score(detail.score)} />
        <Stat
          label="Net profit"
          value={money(economics?.net_profit)}
          tone={Number(economics?.net_profit ?? 0) >= 0 ? "buy" : "pass"}
        />
        <Stat label="ROI" value={<Value>{percent(economics?.roi ?? null)}</Value>} />
        <Stat label="Margin" value={<Value>{percent(economics?.margin ?? null)}</Value>} />
        <Stat
          label="Data quality"
          value={score(detail.data_quality_score)}
          hint={`Match ${percent(detail.match_confidence, 0)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Product identity" subtitle="How we know these are the same product">
          {detail.match ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="numeric text-2xl font-semibold">
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
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted">Evidence</div>
                <ul className="mt-1.5 space-y-1 text-sm">
                  {detail.match.evidence.map((item, index) => (
                    <li key={`${item.type}-${index}`} className="text-muted">
                      {item.detail}
                    </li>
                  ))}
                </ul>
              </div>
              {detail.match.conflicts.length > 0 && (
                <div>
                  <div className="text-2xs uppercase tracking-wide text-pass">Conflicts</div>
                  <ul className="mt-1.5 space-y-1 text-sm text-pass">
                    {detail.match.conflicts.map((conflict, index) => (
                      <li key={`${conflict.dimension}-${index}`}>{conflict.message}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="text-2xs text-muted">
                Variation check:{" "}
                {detail.match.variation_check_passed ? "passed" : "conflicts present"}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted">No stored match record.</p>
          )}
        </Card>

        <Card title="Listings" subtitle="Source and exit market">
          <div className="space-y-4">
            {[
              { label: "Buy from", listing: detail.source_listing },
              { label: "Sell on", listing: detail.target_listing },
            ].map(({ label, listing }) => (
              <div key={label}>
                <div className="text-2xs uppercase tracking-wide text-muted">
                  {label} {listing?.marketplace}
                </div>
                <div className="mt-0.5 text-sm">{listing?.title ?? "not available"}</div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                  <span className="numeric">{money(listing?.current_price)}</span>
                  <span>{titleCase(listing?.availability)}</span>
                  {listing?.seller_count !== null && listing?.seller_count !== undefined && (
                    <span>{listing.seller_count} sellers</span>
                  )}
                  {listing?.sales_rank && <span>Rank {listing.sales_rank.toLocaleString()}</span>}
                  <span className="font-mono text-2xs">{listing?.external_id}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card
        title="Economics"
        subtitle={`Assumptions ${economics?.assumptions_version ?? "unknown"}`}
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
          <p className="text-sm text-muted">No profitability snapshot stored.</p>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Score breakdown" subtitle="No component is hidden">
          <div className="space-y-3">
            {components.map((component) => (
              <div key={component.name}>
                <div className="flex items-baseline justify-between text-sm">
                  <span>{titleCase(component.name)}</span>
                  <span className="numeric">
                    {score(component.score)}
                    <span className="ml-2 text-2xs text-muted">
                      x{percent(component.weight, 0)}
                    </span>
                  </span>
                </div>
                <div className="mt-1">
                  <ScoreBar
                    value={Number(component.score)}
                    tone={component.has_data ? "accent" : "review"}
                  />
                </div>
                <div className="mt-1 text-2xs text-muted">
                  {component.basis}
                  {!component.has_data && " (no data behind this component)"}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Decision gates" subtitle="Hard gates force a pass, soft gates block a buy">
          <div className="space-y-2">
            {gates.map((gate) => (
              <div key={gate.code} className="flex items-start gap-2 text-sm">
                <span className={gate.passed ? "text-buy" : gate.hard ? "text-pass" : "text-review"}>
                  {gate.passed ? "ok" : gate.hard ? "x" : "!"}
                </span>
                <div className="min-w-0">
                  <div>{gate.label}</div>
                  <div className="text-2xs text-muted">{gate.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {detail.stress_test?.scenarios && detail.stress_test.scenarios.length > 0 && (
        <Card title="Stress test" subtitle="What happens when the assumptions move">
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
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Price history" subtitle="Windows report their own sufficiency">
          <div className="space-y-4">
            {(["source", "target"] as const).map((side) => {
              const history = detail.price_history?.[side];
              if (!history) return null;
              return (
                <div key={side}>
                  <div className="flex items-center justify-between">
                    <div className="text-2xs uppercase tracking-wide text-muted">
                      {side} market
                    </div>
                    <ConfidenceBadge value={history.confidence} />
                  </div>
                  <div className="mt-1 text-2xs text-muted">
                    {history.observation_count} observations over {history.history_span_days} days
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
        </Card>

        <Card title="Decision history" subtitle="Every transition is recorded">
          <ol className="space-y-3">
            {detail.events.map((event) => (
              <li key={event.id} className="border-l border-border pl-3">
                <div className="text-sm">
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
                <div className="mt-0.5 text-2xs text-muted">
                  {date(event.created_at)} by {event.actor}
                </div>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
