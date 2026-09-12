import Link from "next/link";

import {
  Card,
  EmptyState,
  ErrorState,
  RecommendationBadge,
  RiskBadge,
  Table,
  Td,
  Th,
  Value,
} from "@/components/ui";
import { endpoints, type OpportunitySummary, type Page } from "@/lib/api";
import { money, percent, relativeDate, score } from "@/lib/format";

export const dynamic = "force-dynamic";

const FILTERS = [
  { key: "", label: "All" },
  { key: "buy", label: "Buy" },
  { key: "review", label: "Review" },
  { key: "pass", label: "Pass" },
];

const SORTS = [
  { key: "score", label: "Score" },
  { key: "profit", label: "Profit" },
  { key: "roi", label: "ROI" },
  { key: "risk", label: "Risk" },
];

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<{ recommendation?: string; sort?: string }>;
}) {
  const params = await searchParams;
  const recommendation = params.recommendation ?? "";
  const sort = params.sort ?? "score";

  const query = new URLSearchParams({ sort, limit: "100" });
  if (recommendation) query.set("recommendation", recommendation);

  let data: Page<OpportunitySummary>;
  try {
    data = await endpoints.opportunities(`?${query.toString()}`);
  } catch (error) {
    return <ErrorState message={`Could not load opportunities. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Opportunities</h1>
          <p className="mt-1 text-sm text-muted">
            {data.total} analysed. Candidates that cannot be bought score zero and sort
            to the bottom.
          </p>
        </div>
        <div className="flex flex-wrap gap-4">
          <div className="flex gap-1">
            {FILTERS.map((filter) => (
              <Link
                key={filter.key || "all"}
                href={`/opportunities?sort=${sort}${filter.key ? `&recommendation=${filter.key}` : ""}`}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  recommendation === filter.key
                    ? "bg-raised text-primary"
                    : "text-muted hover:text-primary"
                }`}
              >
                {filter.label}
              </Link>
            ))}
          </div>
          <div className="flex gap-1 border-l border-border pl-4">
            {SORTS.map((option) => (
              <Link
                key={option.key}
                href={`/opportunities?sort=${option.key}${recommendation ? `&recommendation=${recommendation}` : ""}`}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  sort === option.key ? "bg-raised text-primary" : "text-muted hover:text-primary"
                }`}
              >
                {option.label}
              </Link>
            ))}
          </div>
        </div>
      </div>

      {data.items.length === 0 ? (
        <EmptyState
          title="No opportunities yet"
          description="Analyse a product, or seed the fixture catalogue with 'make seed', and results will appear here."
          action={
            <Link href="/analyze" className="text-sm text-accent hover:underline">
              Analyse a product
            </Link>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th align="right">Score</Th>
                <Th>Product</Th>
                <Th>Call</Th>
                <Th align="right">Buy at</Th>
                <Th align="right">Sell at</Th>
                <Th align="right">Spread</Th>
                <Th align="right">Profit</Th>
                <Th align="right">ROI</Th>
                <Th>Risk</Th>
                <Th align="right">Match</Th>
                <Th align="right">Quality</Th>
                <Th align="right">Analysed</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id} className="transition hover:bg-raised/50">
                  <Td align="right" numeric className="font-semibold">
                    {score(row.score)}
                  </Td>
                  <Td>
                    <Link
                      href={`/opportunities/${row.id}`}
                      className="text-accent transition hover:underline"
                    >
                      <span className="block max-w-xs truncate">
                        {row.title ?? `${row.source_marketplace} to ${row.target_marketplace}`}
                      </span>
                    </Link>
                    <span className="text-2xs text-muted">
                      {row.brand ? `${row.brand} · ` : ""}
                      {row.source_marketplace} to {row.target_marketplace}
                    </span>
                  </Td>
                  <Td>
                    <RecommendationBadge value={row.recommendation} />
                  </Td>
                  <Td align="right" numeric>
                    {money(row.acquisition_cost)}
                  </Td>
                  <Td align="right" numeric>
                    {money(row.expected_sale_price)}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {money(row.spread)}
                  </Td>
                  <Td
                    align="right"
                    numeric
                    className={
                      Number(row.net_profit ?? 0) >= 0 ? "text-buy" : "text-pass"
                    }
                  >
                    {money(row.net_profit)}
                  </Td>
                  <Td align="right" numeric>
                    <Value>{percent(row.roi)}</Value>
                  </Td>
                  <Td>
                    <RiskBadge value={row.risk_level} />
                  </Td>
                  <Td align="right" numeric>
                    <Value>{percent(row.match_confidence, 0)}</Value>
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {score(row.data_quality_score)}
                  </Td>
                  <Td align="right" className="text-2xs text-muted">
                    {relativeDate(row.analyzed_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
