import Link from "next/link";

import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  RiskBadge,
  Stat,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { autonomyApi, type CapitalPositionView, type PortfolioSummary } from "@/lib/api";
import { money, percent, relativeDate, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

const VIEWS = [
  { key: "", label: "All" },
  { key: "open", label: "Open" },
  { key: "closed", label: "Closed" },
];

/**
 * Inventory as an investment portfolio.
 *
 * A purchased product is not a row in a stock list, it is capital committed at
 * a price with an expected return and a holding period. A shadow position is
 * shown alongside a live one deliberately: they are tracked against the same
 * market data and measured the same way, and comparing them is the point.
 */
export default async function PositionsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const status = typeof params.status === "string" ? params.status : "";

  let data: { items: CapitalPositionView[]; total: number; portfolio: PortfolioSummary };
  try {
    data = await autonomyApi.positions(status ? `?status=${status}` : "");
  } catch (error) {
    return <ErrorState message={`Could not load positions. ${(error as Error).message}`} />;
  }

  const { portfolio } = data;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Positions"
        description="Every position is capital committed at a price, with an expected return and a holding period."
        actions={
          <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-raised p-0.5">
            {VIEWS.map((view) => (
              <Link
                key={view.key || "all"}
                href={view.key ? `/autonomy/positions?status=${view.key}` : "/autonomy/positions"}
                className={
                  status === view.key
                    ? "rounded-full bg-surface px-3 py-1 text-2xs text-primary shadow-card"
                    : "rounded-full px-3 py-1 text-2xs text-faint transition hover:text-secondary"
                }
              >
                {view.label}
              </Link>
            ))}
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Capital deployed" value={money(portfolio.capital_deployed)} />
        <Stat
          label="Expected profit"
          value={money(portfolio.expected_profit)}
          hint="on open positions"
        />
        <Stat
          label="Realised profit"
          value={money(portfolio.realized_profit)}
          tone={Number(portfolio.realized_profit) >= 0 ? "buy" : "pass"}
          hint={
            portfolio.realized_roi
              ? `${percent(portfolio.realized_roi)} return`
              : "nothing closed yet"
          }
        />
        <Stat label="Open" value={portfolio.open_positions} hint={`${portfolio.closed_positions} closed`} />
        <Stat
          label="Oldest position"
          value={`${portfolio.oldest_position_days}d`}
          hint={`average ${portfolio.average_age_days}d`}
        />
      </div>

      {data.items.length === 0 ? (
        <EmptyState
          title="No positions yet"
          description="Authorise autonomous capital and run the decision process, and positions will appear here. Shadow positions are tracked the same way as real ones."
          action={
            <Link href="/autonomy" className="text-xs text-accent hover:underline">
              Open autonomy
            </Link>
          }
        />
      ) : (
        <Card flush>
          <Table>
            <thead>
              <tr>
                <Th>Product</Th>
                <Th className="w-[80px]">Mode</Th>
                <Th align="right">Units</Th>
                <Th align="right">Unit cost</Th>
                <Th align="right">Capital</Th>
                <Th align="right">Expected</Th>
                <Th align="right">Realised</Th>
                <Th className="w-[96px]">Risk</Th>
                <Th align="right">Held</Th>
                <Th align="right">Opened</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <Tr key={row.id}>
                  <Td>
                    <div className="max-w-[280px] truncate text-secondary">
                      {row.title ?? "not available"}
                    </div>
                    <div className="mt-0.5 text-2xs text-faint">
                      {row.brand ? `${row.brand} · ` : ""}
                      {titleCase(row.source_marketplace)} to{" "}
                      {titleCase(row.target_marketplace)}
                    </div>
                  </Td>
                  <Td>
                    <span
                      className={`text-2xs ${
                        row.execution_mode === "live" ? "text-accent" : "text-muted"
                      }`}
                    >
                      {row.execution_mode}
                    </span>
                    {row.purchase_id === null && row.execution_mode === "shadow" && (
                      <div className="text-3xs text-faint">nothing bought</div>
                    )}
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    {row.quantity_sold > 0
                      ? `${row.quantity_sold} / ${row.quantity}`
                      : row.quantity}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {money(row.unit_cost)}
                  </Td>
                  <Td align="right" numeric className="text-primary">
                    {money(row.capital_invested)}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    <Value>{row.expected_profit && money(row.expected_profit)}</Value>
                  </Td>
                  <Td
                    align="right"
                    numeric
                    className={
                      Number(row.realized_profit) > 0
                        ? "text-buy"
                        : Number(row.realized_profit) < 0
                          ? "text-pass"
                          : "text-faint"
                    }
                  >
                    {row.status === "closed" ? money(row.realized_profit) : ""}
                  </Td>
                  <Td>{row.risk_level && <RiskBadge value={row.risk_level} />}</Td>
                  <Td align="right" numeric className="text-muted">
                    {row.days_held}d
                  </Td>
                  <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                    {relativeDate(row.opened_at)}
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
