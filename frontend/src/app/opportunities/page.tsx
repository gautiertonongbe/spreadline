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
import { money, percent, relativeDate, score } from "@/lib/format";

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

function profitTone(value: string | null): string {
  if (value === null) return "text-muted";
  return Number(value) >= 0 ? "text-buy" : "text-pass";
}

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;

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

  return (
    <div>
      <PageHeader
        title="Opportunities"
        description="Ranked by score. A candidate that cannot be bought scores zero and sorts to the bottom, so a large apparent spread on a rejected match never reaches the top of this table."
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
