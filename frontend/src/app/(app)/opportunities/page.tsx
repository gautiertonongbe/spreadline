import Link from "next/link";

import { OpportunityFilters } from "@/components/opportunity-filters";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  RecommendationBadge,
  RiskBadge,
  ScoreBar,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { endpoints, type OpportunitySummary, type Page } from "@/lib/api";
import { money, percent, relativeDate, score, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Query keys the API accepts. Anything else in the URL is ignored. */
const PASSTHROUGH = [
  "search",
  "recommendation",
  "risk_level",
  "status",
  "min_score",
  "min_roi",
  "min_profit",
  "marketplace",
  "sort",
  "descending",
];

function buildQuery(params: Record<string, string | string[] | undefined>): string {
  const query = new URLSearchParams();
  for (const key of PASSTHROUGH) {
    const value = params[key];
    if (value === undefined) continue;
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
    else query.set(key, value);
  }
  if (!query.has("sort")) query.set("sort", "score");
  query.set("limit", "100");
  return query.toString();
}

/** The same page with one parameter changed, so a view is a shareable link. */
function withParam(
  params: Record<string, string | string[] | undefined>,
  key: string,
  value: string | null,
): string {
  const query = new URLSearchParams();
  for (const [name, raw] of Object.entries(params)) {
    if (name === key || raw === undefined) continue;
    if (Array.isArray(raw)) raw.forEach((item) => query.append(name, item));
    else query.set(name, raw);
  }
  if (value !== null) query.set(key, value);
  const rendered = query.toString();
  return rendered ? `/opportunities?${rendered}` : "/opportunities";
}

function profitTone(value: string | null): string {
  if (value === null) return "text-muted";
  return Number(value) >= 0 ? "text-buy" : "text-pass";
}

/**
 * One decision, at a glance.
 *
 * A reseller comparing candidates is asking one question: which of these is
 * worth my next hour. That question is answered by the call, the money, and the
 * one thing holding it back. It is not answered any better by thirteen columns,
 * and thirteen columns cost the reader the time the tool was supposed to save,
 * so the full table is still here, one click away, and not in the way.
 */
function DecisionRow({ row }: { row: OpportunitySummary }) {
  const tone =
    row.recommendation === "buy" ? "buy" : row.recommendation === "review" ? "review" : "pass";

  return (
    <Link
      href={`/opportunities/${row.id}`}
      className="group block border-b border-hairline px-5 py-4 transition last:border-b-0 hover:bg-raised/50"
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:gap-6">
        <div className="flex shrink-0 items-center gap-3 lg:w-[112px] lg:flex-col lg:items-start lg:gap-2">
          <RecommendationBadge value={row.recommendation} />
          <div className="flex items-baseline gap-1.5 lg:w-full">
            <span className="numeric text-[0.9375rem] font-medium text-primary">
              {score(row.score)}
            </span>
            <span className="text-3xs uppercase tracking-label text-faint">score</span>
          </div>
          <div className="hidden w-full lg:block">
            <ScoreBar value={Number(row.score ?? 0)} tone={tone} />
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <div className="truncate text-[0.875rem] font-medium text-primary transition group-hover:text-accent">
            {row.title ?? `${row.source_marketplace} to ${row.target_marketplace}`}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-faint">
            {row.brand && <span>{row.brand}</span>}
            <span className="whitespace-nowrap">
              Buy {titleCase(row.source_marketplace)} · Sell {titleCase(row.target_marketplace)}
            </span>
            <RiskBadge value={row.risk_level} />
            <span>{percent(row.match_confidence, 0)} same item</span>
          </div>
          {row.headline && (
            <p className="mt-2 line-clamp-2 max-w-2xl text-xs leading-relaxed text-muted">
              {row.headline}
            </p>
          )}
          {row.primary_blocker && (
            <div className="mt-2 inline-flex items-center gap-2 rounded bg-raised px-2 py-1 text-2xs text-secondary">
              <span className="h-1.5 w-1.5 rounded-full bg-review" />
              {row.primary_blocker}
            </div>
          )}
        </div>

        <div className="grid shrink-0 grid-cols-3 gap-x-6 text-right lg:w-[300px]">
          <div>
            <div className="label">You make</div>
            <div className={`numeric mt-1 text-[0.9375rem] ${profitTone(row.net_profit)}`}>
              {money(row.net_profit)}
            </div>
          </div>
          <div>
            <div className="label">Return</div>
            <div className="numeric mt-1 text-[0.9375rem] text-secondary">
              <Value>{percent(row.roi)}</Value>
            </div>
          </div>
          <div>
            <div className="label">Max to pay</div>
            <div className="numeric mt-1 text-[0.9375rem] text-secondary">
              <Value>{money(row.max_acquisition_cost)}</Value>
            </div>
          </div>
        </div>
      </div>
    </Link>
  );
}

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const view = params.view === "full" ? "full" : "quick";

  let data: Page<OpportunitySummary>;
  try {
    data = await endpoints.opportunities(`?${buildQuery(params)}`);
  } catch (error) {
    return (
      <ErrorState message={`Could not load opportunities. ${(error as Error).message}`} />
    );
  }

  const hasFilters = PASSTHROUGH.some(
    (key) => key !== "sort" && key !== "descending" && params[key] !== undefined,
  );

  const views: { key: "quick" | "full"; label: string; href: string }[] = [
    { key: "quick", label: "Decisions", href: withParam(params, "view", null) },
    { key: "full", label: "Full table", href: withParam(params, "view", "full") },
  ];

  return (
    <div>
      <PageHeader
        title="Opportunities"
        description="What to buy, what to check first, and what to skip. Anything you could not actually buy is scored zero and sinks to the bottom."
        actions={
          <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-raised p-0.5">
            {views.map((item) => (
              <Link
                key={item.key}
                href={item.href}
                className={
                  view === item.key
                    ? "rounded-full bg-surface px-3 py-1 text-2xs text-primary shadow-card"
                    : "rounded-full px-3 py-1 text-2xs text-faint transition hover:text-secondary"
                }
              >
                {item.label}
              </Link>
            ))}
          </div>
        }
      />

      <div className="mb-5">
        <OpportunityFilters total={data.total} />
      </div>

      {data.items.length === 0 ? (
        <EmptyState
          title={hasFilters ? "Nothing matches those filters" : "No opportunities yet"}
          description={
            hasFilters
              ? "Every stored opportunity was excluded by the current filters. Clear one, or widen a threshold."
              : "Analyse a product, or seed the fixture catalogue with 'make seed', and results will appear here."
          }
          action={
            hasFilters ? (
              <Link href="/opportunities" className="text-xs text-accent hover:underline">
                Clear all filters
              </Link>
            ) : (
              <Link href="/analyze" className="text-xs text-accent hover:underline">
                Analyse a product
              </Link>
            )
          }
        />
      ) : view === "quick" ? (
        <Card flush>
          {data.items.map((row) => (
            <DecisionRow key={row.id} row={row} />
          ))}
        </Card>
      ) : (
        <Card flush>
          <Table>
            <thead>
              <tr>
                <Th align="right" className="w-[96px]">
                  Score
                </Th>
                <Th>Product</Th>
                <Th className="w-[76px]">Call</Th>
                <Th align="right">Buy at</Th>
                <Th align="right">Sell at</Th>
                <Th align="right">Spread</Th>
                <Th align="right">Profit</Th>
                <Th align="right">ROI</Th>
                <Th align="right">Margin</Th>
                <Th className="w-[96px]">Risk</Th>
                <Th align="right">Match</Th>
                <Th align="right">Quality</Th>
                <Th align="right">Analysed</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <Tr key={row.id}>
                  <Td align="right" numeric>
                    <div className="text-[0.9375rem] font-medium text-primary">
                      {score(row.score)}
                    </div>
                    <div className="mt-1.5">
                      <ScoreBar
                        value={Number(row.score ?? 0)}
                        tone={
                          row.recommendation === "buy"
                            ? "buy"
                            : row.recommendation === "review"
                              ? "review"
                              : "pass"
                        }
                      />
                    </div>
                  </Td>
                  <Td>
                    <Link
                      href={`/opportunities/${row.id}`}
                      className="block max-w-[340px] truncate text-secondary transition hover:text-accent"
                    >
                      {row.title ??
                        `${row.source_marketplace} to ${row.target_marketplace}`}
                    </Link>
                    <div className="mt-0.5 text-2xs text-faint">
                      {row.brand ? `${row.brand} · ` : ""}
                      {row.source_marketplace} to {row.target_marketplace}
                    </div>
                  </Td>
                  <Td>
                    <RecommendationBadge value={row.recommendation} />
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    {money(row.acquisition_cost)}
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    {money(row.expected_sale_price)}
                  </Td>
                  <Td align="right" numeric className="text-faint">
                    {money(row.spread)}
                  </Td>
                  <Td align="right" numeric className={profitTone(row.net_profit)}>
                    {money(row.net_profit)}
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    <Value>{percent(row.roi)}</Value>
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    <Value>{percent(row.margin)}</Value>
                  </Td>
                  <Td>
                    <RiskBadge value={row.risk_level} />
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    <Value>{percent(row.match_confidence, 0)}</Value>
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {score(row.data_quality_score)}
                  </Td>
                  <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                    {relativeDate(row.analyzed_at)}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
