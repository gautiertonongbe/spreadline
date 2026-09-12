"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  ConfidenceBadge,
  ErrorState,
  PageHeader,
  RecommendationBadge,
  RiskBadge,
  ScoreBar,
  Stat,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { endpoints, type AnalysisResponse, type SearchItem } from "@/lib/api";
import { money, percent, score, signedMoney, titleCase } from "@/lib/format";

/**
 * The analyse workflow (spec §26):
 *
 *   search -> pick a source listing -> pick an exit market -> full analysis
 *
 * The exit listing can be left to the platform, which looks it up by identifier
 * and falls back to title search only when it must, saying so in the result.
 */
export default function AnalyzePage() {
  const [query, setQuery] = useState("");
  const [marketplace, setMarketplace] = useState("walmart");
  const [results, setResults] = useState<SearchItem[] | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    setAnalysis(null);
    try {
      const response = await endpoints.search({ query, marketplace, limit: 12 });
      setResults(response.items);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function analyze(item: SearchItem) {
    setBusy(true);
    setError(null);
    try {
      const response = await endpoints.analyze({
        source_marketplace: item.marketplace,
        source_external_id: item.external_id,
        target_marketplace: item.marketplace === "amazon" ? "walmart" : "amazon",
      });
      setAnalysis(response);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Analyze"
        description="Search a marketplace, then analyse a listing against the other market. Keywords, ASINs, Walmart item ids, UPCs and product URLs all work."
      />

      <Card>
        <div className="flex flex-wrap gap-2">
          <select
            value={marketplace}
            onChange={(event) => setMarketplace(event.target.value)}
            className="rounded border border-border bg-canvas py-[9px] pl-3 text-xs text-secondary outline-none transition focus:border-accent/50"
          >
            <option value="walmart">Buy from Walmart</option>
            <option value="amazon">Buy from Amazon</option>
          </select>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void search();
            }}
            placeholder="Sony headphones, B09XS7JWHH, 027242923058, or a product URL"
            className="min-w-[280px] flex-1 rounded border border-border bg-canvas px-3 py-[9px] text-xs text-primary outline-none transition focus:border-accent/50"
          />
          <Button tone="accent" onClick={() => void search()} disabled={busy}>
            {busy ? "Working" : "Search"}
          </Button>
        </div>
      </Card>

      {error && <ErrorState message={error} />}

      {results && results.length > 0 && !analysis && (
        <Card title="Results" subtitle="Pick a listing to analyse" flush>
          <Table>
            <thead>
              <tr>
                <Th>Title</Th>
                <Th>Brand</Th>
                <Th align="right">Price</Th>
                <Th align="right">Rank</Th>
                <Th align="right">Sellers</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {results.map((item) => (
                <Tr key={`${item.marketplace}-${item.external_id}`}>
                  <Td>
                    <div className="max-w-md truncate">{item.title}</div>
                    <div className="mt-0.5 font-mono text-2xs text-faint">{item.external_id}</div>
                  </Td>
                  <Td className="text-muted">{item.brand ?? "not available"}</Td>
                  <Td align="right" numeric>
                    {money(item.price)}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {item.sales_rank?.toLocaleString() ?? "not available"}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {item.seller_count ?? "not available"}
                  </Td>
                  <Td align="right">
                    <Button size="small" onClick={() => void analyze(item)} disabled={busy}>
                      Analyse
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {results && results.length === 0 && (
        <Card>
          <p className="text-sm text-muted">
            Nothing matched that search on {marketplace}.
          </p>
        </Card>
      )}

      {analysis && <AnalysisView analysis={analysis} onBack={() => setAnalysis(null)} />}
    </div>
  );
}

function AnalysisView({
  analysis,
  onBack,
}: {
  analysis: AnalysisResponse;
  onBack: () => void;
}) {
  const { decision, economics, score: scoring, risk, match, summary, stress_test } = analysis;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <RecommendationBadge value={decision.recommendation} />
          <RiskBadge value={risk.level} />
          <Badge>{summary.direction.replace(/_/g, " ")}</Badge>
          {!summary.is_live_data && <Badge tone="warning">Fixture data</Badge>}
        </div>
        <div className="flex gap-2">
          <Button onClick={onBack}>Back to results</Button>
          {analysis.opportunity_id && (
            <Link
              href={`/opportunities/${analysis.opportunity_id}`}
              className="inline-flex items-center rounded border border-accent/30 bg-accent/10 px-3.5 py-[7px] text-xs font-medium text-accent transition hover:bg-accent/20"
            >
              Open full record
            </Link>
          )}
        </div>
      </div>

      <Card title={summary.title} subtitle={decision.headline}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Score" value={score(scoring.total)} />
          <Stat
            label="Net profit"
            value={money(economics.net_profit)}
            tone={Number(economics.net_profit) >= 0 ? "buy" : "pass"}
          />
          <Stat label="ROI" value={<Value>{percent(economics.roi)}</Value>} />
          <Stat
            label="Spread"
            value={money(economics.spread ?? null)}
            hint="Before any fees"
          />
          <Stat
            label="Break-even"
            value={money(economics.breakeven_sale_price)}
            hint="Exit price at zero profit"
          />
        </div>

        {scoring.gated_reason && (
          <div className="mt-4 rounded border border-pass/30 bg-pass/10 px-3 py-2 text-sm text-pass">
            Score withheld: {scoring.gated_reason}
          </div>
        )}

        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <div>
            <div className="label">Why</div>
            <ul className="mt-1.5 space-y-1 text-sm">
              {decision.reasons.length === 0 && (
                <li className="text-muted">No positive findings.</li>
              )}
              {decision.reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <span className="text-buy">+</span>
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="label">Risks</div>
            <ul className="mt-1.5 space-y-1 text-sm">
              {risk.signals.length === 0 && <li className="text-muted">None raised.</li>}
              {risk.signals.map((signal) => (
                <li key={signal.code} className="flex gap-2">
                  <span className={signal.blocking ? "text-pass" : "text-review"}>
                    {signal.blocking ? "x" : "!"}
                  </span>
                  <span>{signal.message}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Identity" subtitle={match.summary}>
          <div className="flex items-center justify-between">
            <div className="numeric text-2xl font-semibold">
              {percent(match.confidence, 0)}
            </div>
            <Badge tone={match.status === "confirmed" ? "success" : "warning"}>
              {titleCase(match.status)} via {titleCase(match.method)}
            </Badge>
          </div>
          <ul className="mt-3 space-y-1 text-sm text-muted">
            {match.evidence.map((item, index) => (
              <li key={index}>{item.detail}</li>
            ))}
          </ul>
          {match.conflicts.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-pass">
              {match.conflicts.map((conflict, index) => (
                <li key={index}>{conflict.message}</li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Demand and competition">
          <div className="space-y-4">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-sm">Demand</span>
                <ConfidenceBadge value={analysis.demand.confidence} />
              </div>
              <div className="numeric mt-1 text-lg">
                <Value>{score(analysis.demand.score)}</Value>
              </div>
              <ul className="mt-1 space-y-0.5 text-2xs text-muted">
                {analysis.demand.reasons.slice(0, 3).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
            <div className="border-t border-border pt-3">
              <div className="flex items-center justify-between">
                <span className="text-sm">Competition</span>
                <RiskBadge value={analysis.competition.risk_level} />
              </div>
              <div className="numeric mt-1 text-lg">
                {analysis.competition.seller_count ?? "not available"}
                <span className="ml-2 text-xs text-muted">sellers</span>
              </div>
              <ul className="mt-1 space-y-0.5 text-2xs text-muted">
                {analysis.competition.reasons.slice(0, 3).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      </div>

      <Card title="Score breakdown" subtitle="No component is hidden">
        <div className="grid gap-3 lg:grid-cols-2">
          {scoring.components.map((component) => (
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
              <div className="mt-0.5 text-2xs text-muted">{component.basis}</div>
            </div>
          ))}
        </div>
      </Card>

      {stress_test && (
        <Card
          title="Stress test"
          subtitle={`${stress_test.surviving_count} of ${stress_test.total_count} adverse scenarios stay profitable. Exit price can fall ${percent(stress_test.price_headroom, 0)} before break-even.`}
        >
          <Table>
            <thead>
              <tr>
                <Th>Scenario</Th>
                <Th>Assumption</Th>
                <Th align="right">Sale price</Th>
                <Th align="right">Profit</Th>
                <Th align="right">Change</Th>
                <Th align="right">ROI</Th>
              </tr>
            </thead>
            <tbody>
              {stress_test.scenarios.map((scenario) => (
                <Tr key={scenario.key}>
                  <Td>{scenario.label}</Td>
                  <Td className="text-2xs text-muted">{scenario.description}</Td>
                  <Td align="right" numeric>
                    {money(scenario.sale_price)}
                  </Td>
                  <Td
                    align="right"
                    numeric
                    className={scenario.survives ? "text-buy" : "text-pass"}
                  >
                    {money(scenario.net_profit)}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {scenario.key === "base" ? "" : signedMoney(scenario.profit_delta)}
                  </Td>
                  <Td align="right" numeric>
                    <Value>{percent(scenario.roi)}</Value>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {analysis.notes.length > 0 && (
        <Card title="Notes from this analysis">
          <ul className="space-y-1 text-xs text-muted">
            {analysis.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
