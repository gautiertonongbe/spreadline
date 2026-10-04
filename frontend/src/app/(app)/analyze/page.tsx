"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  ConfidenceBadge,
  Disclosure,
  ErrorState,
  MarketStrip,
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
  Verdict,
} from "@/components/ui";
import {
  RequestFailed,
  endpoints,
  type AnalysisResponse,
  type ManualEntryReport,
  type ScoreBreakdown,
  type SearchItem,
} from "@/lib/api";
import { ManualEntry, ManualGaps } from "@/components/manual-entry";
import {
  RiskCategories,
  ScoreTable,
  SpreadEvidencePanel,
  SpreadLadder,
} from "@/components/evidence";
import {
  isOpenableUrl,
  money,
  percent,
  score,
  signedMoney,
  titleCase,
} from "@/lib/format";

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
  const [noExit, setNoExit] = useState<{
    item: SearchItem;
    exit: string;
  } | null>(null);
  // "look" searches the providers; "type" takes a pair the person read off two
  // product pages themselves. The second needs no credential, which is why it is
  // the one that works today.
  const [mode, setMode] = useState<"look" | "type">("look");
  const [entry, setEntry] = useState<ManualEntryReport | null>(null);

  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    setAnalysis(null);
    setNoExit(null);
    try {
      const response = await endpoints.search({
        query,
        marketplace,
        limit: 12,
      });
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
    setNoExit(null);
    const exit = item.marketplace === "amazon" ? "walmart" : "amazon";
    try {
      const response = await endpoints.analyze({
        source_marketplace: item.marketplace,
        source_external_id: item.external_id,
        target_marketplace: exit,
      });
      setAnalysis(response);
    } catch (caught) {
      // Not finding the item on the other market is an answer, not a failure.
      // It used to surface as a red API error, which read as "the tool is
      // broken" when what actually happened is "there is nowhere to sell this".
      if (caught instanceof RequestFailed && caught.code === "not_found") {
        setNoExit({ item, exit });
      } else {
        setError((caught as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Analyze"
        description="Find something you could buy, and we will tell you what it sells for elsewhere and what you would make."
        actions={
          <div className="flex rounded border border-border p-0.5">
            {(
              [
                ["look", "Look it up"],
                ["type", "Type it in"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setMode(value);
                  setAnalysis(null);
                  setEntry(null);
                  setNoExit(null);
                }}
                className={`rounded px-3 py-1.5 text-xs transition ${
                  mode === value
                    ? "bg-raised text-primary"
                    : "text-muted hover:text-secondary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        }
      />

      {mode === "type" ? (
        <ManualEntry
          onAnalysed={(body, report) => {
            setAnalysis(body);
            setEntry(report);
            setNoExit(null);
          }}
        />
      ) : (
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
      )}

      {error && <ErrorState message={error} />}

      {results && results.length > 0 && !analysis && !noExit && (
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
                    <div className="mt-0.5 font-mono text-2xs text-faint">
                      {item.external_id}
                    </div>
                  </Td>
                  <Td className="text-muted">
                    {item.brand ?? "not available"}
                  </Td>
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
                    <Button
                      size="small"
                      onClick={() => void analyze(item)}
                      disabled={busy}
                    >
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
          <p className="text-[0.8125rem] leading-relaxed text-secondary">
            Nothing on {titleCase(marketplace)} matched that. Try the brand and
            model together, or paste the product link or its barcode number.
          </p>
        </Card>
      )}

      {noExit && (
        <NoExitMarket
          item={noExit.item}
          exit={noExit.exit}
          onBack={() => setNoExit(null)}
        />
      )}

      {entry && <ManualGaps entry={entry} />}

      {analysis && (
        <AnalysisView
          analysis={analysis}
          onBack={() => {
            setAnalysis(null);
            setEntry(null);
          }}
        />
      )}
    </div>
  );
}
/**
 * We found the item, but nowhere to sell it.
 *
 * This used to surface as a red API error with a 404 message in it, which reads
 * as "the tool is broken" when what actually happened is a real answer: without
 * a price on the other side there is no profit to work out, and inventing one is
 * exactly what this platform refuses to do.
 */
function NoExitMarket({
  item,
  exit,
  onBack,
}: {
  item: SearchItem;
  exit: string;
  onBack: () => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface shadow-card">
      <div className="px-6 pb-5 pt-6">
        <div className="label">The call</div>
        <div className="display mt-1.5 text-[2.5rem] font-medium leading-none tracking-[-0.02em] text-muted">
          Cannot price it
        </div>
        <p className="mt-3 max-w-xl text-[0.875rem] leading-relaxed text-secondary">
          We could not find <span className="text-primary">{item.title}</span>{" "}
          on {titleCase(exit)}. You buy it for {money(item.price)}, but without
          a price on the other side there is nothing to work a profit out from,
          and we will not guess one.
        </p>
        <p className="mt-2 max-w-xl text-xs leading-relaxed text-muted">
          It may be listed there under a different title. Searching by its
          barcode or model number finds it when a title search cannot.
        </p>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-hairline px-6 py-3">
        <button
          type="button"
          onClick={onBack}
          className="text-xs text-accent transition hover:underline"
        >
          Back to results
        </button>
        {isOpenableUrl(item.url) && (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-xs text-muted transition hover:text-primary"
          >
            Open it on {titleCase(item.marketplace)}
          </a>
        )}
      </div>
    </section>
  );
}

/**
 * One product, one answer.
 *
 * This is the screen a reseller lands on with a listing open in another tab and
 * a minute to decide. It answers the call, the money and the blocker without
 * scrolling; everything the engines computed is still here, folded below, for
 * the second pass and the audit. Nothing is summarised away, it is just ordered
 * by how quickly it is needed.
 */
function AnalysisView({
  analysis,
  onBack,
}: {
  analysis: AnalysisResponse;
  onBack: () => void;
}) {
  const {
    decision,
    economics,
    score: scoring,
    risk,
    match,
    summary,
    stress_test,
  } = analysis;

  const failedGates = decision.gates
    .filter((gate) => !gate.passed)
    .sort((a, b) => Number(b.hard) - Number(a.hard));
  const blockers = [
    ...(scoring.gated_reason
      ? [{ text: `Score withheld. ${scoring.gated_reason}`, hard: true }]
      : []),
    ...failedGates.map((gate) => ({
      text: `${gate.label}. ${gate.detail}`,
      hard: gate.hard,
    })),
    ...risk.signals
      .filter((signal) => signal.blocking)
      .map((signal) => ({ text: signal.message, hard: true })),
  ];
  const passedGates = decision.gates.length - failedGates.length;

  const asSide = (side: typeof summary.source) => ({
    marketplace: titleCase(side.marketplace),
    price: side.price,
    externalId: side.external_id,
    availability: side.availability,
    sellerCount: side.seller_count ?? null,
    url: side.url,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="display truncate text-[1.625rem] font-medium leading-tight tracking-tight text-primary">
            {summary.title}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2.5 text-2xs text-faint">
            {summary.brand && <span>{summary.brand}</span>}
            <RiskBadge value={risk.level} />
            <span>Data quality {score(analysis.data_quality.score)}</span>
            {!summary.is_live_data && (
              <Badge tone="warning">
                Fixture data, not a market observation
              </Badge>
            )}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
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

      <Verdict
        recommendation={decision.recommendation}
        headline={decision.headline}
        blockers={blockers}
        numbers={[
          {
            label: "You make",
            value: money(economics.net_profit),
            tone: Number(economics.net_profit) >= 0 ? "buy" : "pass",
            hint: `On each one, after every fee. You pay ${money(
              economics.acquisition_cost,
            )} and ${money(economics.total_fees)} goes in fees.`,
          },
          {
            label: "Back on your money",
            value: <Value>{percent(economics.roi)}</Value>,
            hint: `You keep ${percent(economics.margin)} of the ${money(
              economics.sale_price,
            )} it sells for.`,
          },
          {
            label: "Do not pay over",
            value: <Value>{money(economics.max_acquisition_cost)}</Value>,
            hint: `Pay more than this and you stop making money, even at the full ${money(
              economics.sale_price,
            )} sale price.`,
          },
        ]}
      />

      <MarketStrip
        source={asSide(summary.source)}
        target={asSide(summary.target)}
        matchConfidence={String(match.confidence)}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <SpreadLadder
          economics={economics}
          sourceMarketplace={summary.source.marketplace}
          targetMarketplace={summary.target.marketplace}
        />
        {analysis.spread_evidence && (
          <SpreadEvidencePanel evidence={analysis.spread_evidence} />
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
            summary={`${decision.reasons.length} supporting, ${risk.signals.length} risk signal(s), ${passedGates} of ${decision.gates.length} gates passed`}
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div className="space-y-4">
                <div>
                  <div className="label">Supporting</div>
                  <ul className="mt-2 space-y-1.5 text-[0.8125rem] leading-relaxed">
                    {decision.reasons.length === 0 && (
                      <li className="text-muted">No positive findings.</li>
                    )}
                    {decision.reasons.map((reason) => (
                      <li key={reason} className="flex gap-2.5">
                        <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-buy" />
                        <span className="text-secondary">{reason}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="label">Risk signals</div>
                  <ul className="mt-2 space-y-1.5 text-[0.8125rem] leading-relaxed">
                    {risk.signals.length === 0 && (
                      <li className="text-muted">None raised.</li>
                    )}
                    {risk.signals.map((signal) => (
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
              </div>
              <div>
                <div className="label">Decision gates</div>
                <div className="mt-2 space-y-2">
                  {decision.gates.map((gate) => (
                    <div
                      key={gate.code}
                      className="flex items-start gap-2.5 text-[0.8125rem]"
                    >
                      <span
                        className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${
                          gate.passed
                            ? "bg-buy"
                            : gate.hard
                              ? "bg-pass"
                              : "bg-review"
                        }`}
                      />
                      <div className="min-w-0">
                        <div className="text-secondary">{gate.label}</div>
                        <div className="text-2xs text-muted">{gate.detail}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Risk, by kind"
            summary={`${risk.categories.filter((item) => item.has_evidence).length} of ${risk.categories.length} assessed${risk.driving_category ? `, led by ${risk.driving_category.replace(/_/g, " ")}` : ""}`}
          >
            <div className="space-y-4">
              <p className="text-[0.8125rem] leading-relaxed text-secondary">
                {risk.summary}
              </p>
              <RiskCategories risk={risk} />
            </div>
          </Disclosure>

          <Disclosure
            title="The money"
            summary={`${money(economics.sale_price)} sale, ${money(
              economics.total_fees,
            )} fees, ${money(economics.net_profit)} net`}
          >
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
                        className={
                          item.sign > 0 ? "text-primary" : "text-muted"
                        }
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
                        Number(economics.net_profit) >= 0
                          ? "text-buy"
                          : "text-pass"
                      }`}
                    >
                      {money(economics.net_profit)}
                    </Td>
                  </tr>
                </tbody>
              </Table>
              <div className="space-y-3">
                <Stat
                  label="Spread before fees"
                  value={money(economics.spread ?? null)}
                />
                <Stat
                  label="Break-even sale price"
                  value={money(economics.breakeven_sale_price)}
                  hint="Exit price at zero profit"
                />
                <Stat label="Total fees" value={money(economics.total_fees)} />
                <div className="text-2xs text-faint">
                  Fee assumptions {economics.assumptions_version}, frozen into
                  this analysis.
                </div>
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Product identity"
            summary={`${percent(match.confidence, 0)} via ${titleCase(match.method)}, ${
              match.conflicts.length
            } conflict(s)`}
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <div className="flex items-center justify-between">
                  <div className="numeric text-2xl text-primary">
                    {percent(match.confidence, 0)}
                  </div>
                  <Badge
                    tone={match.status === "confirmed" ? "success" : "warning"}
                  >
                    {titleCase(match.status)} via {titleCase(match.method)}
                  </Badge>
                </div>
                <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted">
                  {match.summary}
                </p>
                <p className="mt-2 text-2xs leading-relaxed text-faint">
                  Title similarity was {percent(match.title_similarity, 0)}, and
                  on its own it can never reach high confidence.
                </p>
              </div>
              <div className="space-y-3">
                <div>
                  <div className="label">Evidence</div>
                  <ul className="mt-1.5 space-y-1 text-[0.8125rem] text-muted">
                    {match.evidence.map((item, index) => (
                      <li key={index}>{item.detail}</li>
                    ))}
                  </ul>
                </div>
                {match.conflicts.length > 0 && (
                  <div>
                    <div className="label text-pass">Conflicts</div>
                    <ul className="mt-1.5 space-y-1 text-[0.8125rem] text-pass">
                      {match.conflicts.map((conflict, index) => (
                        <li key={index}>{conflict.message}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Demand and competition"
            summary={`Demand ${analysis.demand.confidence}, ${
              analysis.competition.seller_count ?? "unknown"
            } sellers`}
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <div className="flex items-center justify-between">
                  <span className="label">Demand</span>
                  <ConfidenceBadge value={analysis.demand.confidence} />
                </div>
                <div className="numeric mt-1.5 text-lg text-primary">
                  <Value>{score(analysis.demand.score)}</Value>
                </div>
                <ul className="mt-1.5 space-y-0.5 text-2xs text-muted">
                  {analysis.demand.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <span className="label">Competition</span>
                  <RiskBadge value={analysis.competition.risk_level} />
                </div>
                <div className="numeric mt-1.5 text-lg text-primary">
                  <Value>
                    {analysis.competition.seller_count === null
                      ? null
                      : `${analysis.competition.seller_count} sellers`}
                  </Value>
                </div>
                <ul className="mt-1.5 space-y-0.5 text-2xs text-muted">
                  {analysis.competition.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            </div>
          </Disclosure>

          <Disclosure
            title="Score breakdown"
            summary={`${scoring.components.length} weighted components, every calculation shown`}
          >
            <ScoreTable breakdown={scoring as ScoreBreakdown} />
          </Disclosure>

          {stress_test && (
            <Disclosure
              title="Stress test"
              summary={`${stress_test.surviving_count} of ${stress_test.total_count} adverse scenarios stay profitable`}
            >
              <p className="mb-3 text-2xs text-muted">
                Exit price can fall {percent(stress_test.price_headroom, 0)}{" "}
                before break-even.
              </p>
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
                      <Td className="text-2xs text-muted">
                        {scenario.description}
                      </Td>
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
                        {scenario.key === "base"
                          ? ""
                          : signedMoney(scenario.profit_delta)}
                      </Td>
                      <Td align="right" numeric>
                        <Value>{percent(scenario.roi)}</Value>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Disclosure>
          )}

          {analysis.notes.length > 0 && (
            <Disclosure
              title="Notes from this analysis"
              summary={`${analysis.notes.length} note(s)`}
            >
              <ul className="space-y-1 text-xs text-muted">
                {analysis.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Disclosure>
          )}
        </div>
      </div>
    </div>
  );
}
