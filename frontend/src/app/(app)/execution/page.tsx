import Link from "next/link";

import { RecordExecution } from "@/components/execution-controls";
import { Badge, Card, EmptyState, ErrorState, Note, PageHeader, Stat } from "@/components/ui";
import { executionApi, type ExecutionReport } from "@/lib/api";
import { isOpenableUrl, money, relativeDate, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

const VARIANCE_LABEL: Record<string, string> = {
  PAID_ABOVE_CEILING: "paid above the ceiling",
  OVER_QUANTITY: "more units than authorised",
  UNDER_QUANTITY: "fewer units than authorised",
  EXPIRED_WHEN_EXECUTED: "bought after it expired",
};

/**
 * The one page where the product asks a person to go and do something.
 *
 * Everything else here decides. This hands the decision across the boundary and
 * takes the answer back, and the answer is what corrects the record: a position
 * left at the figures that were authorised makes every realised number
 * afterwards a measurement of a purchase that never happened.
 */
export default async function ExecutionPage() {
  let report: ExecutionReport;
  try {
    report = await executionApi.overview();
  } catch (error) {
    return <ErrorState message={`Could not load the queue. ${(error as Error).message}`} />;
  }

  const closed = report.recent.filter((row) => row.status !== "pending");

  return (
    <div className="space-y-5">
      <PageHeader
        title="What to buy, and what you paid"
        description="Spreadline decides what, how many and at what maximum price. Placing the order is yours. Recording what you actually paid is what keeps every number after it honest."
        actions={
          <Link href="/autonomy/allocation" className="text-xs text-accent hover:underline">
            What to buy next
          </Link>
        }
      />

      <section className="rounded-xl border border-border bg-surface px-6 py-5 shadow-card">
        <div className="label">Where this stands</div>
        <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-secondary">
          {report.summary}
        </p>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat
          label="Waiting on you"
          value={report.counts.pending}
          tone={report.counts.pending > 0 ? "review" : "muted"}
        />
        <Stat label="Closed out" value={report.counts.closed} />
        <Stat
          label="Departed from the authorisation"
          value={report.counts.with_variance}
          tone={report.counts.with_variance > 0 ? "pass" : "muted"}
          hint="of the closed ones"
        />
      </div>

      {report.outstanding.length === 0 ? (
        <EmptyState
          title="Nothing to buy right now"
          description="An authorised decision appears here with the quantity and the most you may pay. Nothing is bought automatically, and nothing here places an order."
        />
      ) : (
        <div className="space-y-3">
          {report.outstanding.map((instruction) => (
            <Card
              key={instruction.id}
              title={instruction.title ?? "Unnamed product"}
              subtitle={
                instruction.source_marketplace
                  ? `on ${titleCase(instruction.source_marketplace)}`
                  : undefined
              }
              actions={
                instruction.is_expired ? (
                  <Badge tone="danger">Expired</Badge>
                ) : (
                  <Badge tone="warning">Waiting on you</Badge>
                )
              }
            >
              <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
                <div className="space-y-3">
                  <p className="text-[0.9375rem] leading-relaxed text-secondary">
                    {instruction.summary}
                  </p>
                  {isOpenableUrl(instruction.source_url) ? (
                    <a
                      href={instruction.source_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-block text-xs text-accent hover:underline"
                    >
                      Open the listing
                    </a>
                  ) : (
                    <span className="text-2xs text-faint">No live listing to open</span>
                  )}
                  <RecordExecution instruction={instruction} />
                </div>
                <div className="space-y-2">
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Buy up to</span>
                    <span className="numeric text-[0.8125rem] text-primary">
                      {instruction.quantity_authorized} unit(s)
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Pay no more than</span>
                    <span className="numeric text-[0.8125rem] text-primary">
                      {money(instruction.max_unit_price)}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Capital authorised</span>
                    <span className="numeric text-[0.8125rem] text-secondary">
                      {money(instruction.capital_authorized)}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between py-1.5">
                    <span className="text-2xs text-faint">Good until</span>
                    <span className="text-2xs text-muted">
                      {relativeDate(instruction.expires_at)}
                    </span>
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {closed.length > 0 && (
        <Card
          title="What was actually done"
          subtitle="Authorised against executed. The departures are named rather than absorbed."
          flush
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hairline text-2xs text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-normal">Product</th>
                  <th className="px-3 py-2.5 font-normal">Outcome</th>
                  <th className="px-3 py-2.5 text-right font-normal">Authorised</th>
                  <th className="px-3 py-2.5 text-right font-normal">Bought</th>
                  <th className="px-3 py-2.5 text-right font-normal">Ceiling</th>
                  <th className="px-3 py-2.5 text-right font-normal">Paid</th>
                  <th className="px-5 py-2.5 font-normal">Departures</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((row) => (
                  <tr key={row.id} className="border-b border-hairline last:border-0">
                    <td className="max-w-[220px] truncate px-5 py-2.5 text-secondary">
                      {row.title ?? "Unnamed product"}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{row.status.replace(/_/g, " ")}</td>
                    <td className="numeric px-3 py-2.5 text-right text-muted">
                      {row.quantity_authorized}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-primary">
                      {row.quantity_executed}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-muted">
                      {money(row.max_unit_price)}
                    </td>
                    <td
                      className={`numeric px-3 py-2.5 text-right ${
                        row.variances.includes("PAID_ABOVE_CEILING")
                          ? "text-pass"
                          : "text-secondary"
                      }`}
                    >
                      {row.unit_price_paid ? money(row.unit_price_paid) : "—"}
                    </td>
                    <td className="px-5 py-2.5 text-muted">
                      {row.variances.length === 0 ? (
                        <span className="text-faint">as authorised</span>
                      ) : (
                        row.variances
                          .map((code) => VARIANCE_LABEL[code] ?? code.toLowerCase())
                          .join(", ")
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Note>{report.note}</Note>
    </div>
  );
}
