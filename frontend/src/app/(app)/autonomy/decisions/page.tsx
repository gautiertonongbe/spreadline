import Link from "next/link";

import { OutcomeBadge, VerdictDot } from "@/components/autonomy-ui";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { autonomyApi, type DecisionSummary } from "@/lib/api";
import { money, relativeDate } from "@/lib/format";

export const dynamic = "force-dynamic";

const FILTERS = [
  { key: "", label: "All" },
  { key: "authorized", label: "Authorised" },
  { key: "blocked", label: "Blocked" },
  { key: "escalated", label: "Escalated" },
];

/**
 * Every decision, authorised or not.
 *
 * The refusals are the point of this page as much as the authorisations. A
 * system that only listed its purchases could not demonstrate its restraint,
 * and the reason code on a refusal is what shows which limit did the work.
 */
export default async function DecisionsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const outcome = typeof params.outcome === "string" ? params.outcome : "";

  let data: { items: DecisionSummary[]; total: number };
  try {
    data = await autonomyApi.decisions(outcome ? `?outcome=${outcome}` : "");
  } catch (error) {
    return <ErrorState message={`Could not load decisions. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Decisions"
        description="Every decision the autonomy engine reached, and why. Refusals are kept as carefully as authorisations."
        actions={
          <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-raised p-0.5">
            {FILTERS.map((filter) => (
              <Link
                key={filter.key || "all"}
                href={filter.key ? `/autonomy/decisions?outcome=${filter.key}` : "/autonomy/decisions"}
                className={
                  outcome === filter.key
                    ? "rounded-full bg-surface px-3 py-1 text-2xs text-primary shadow-card"
                    : "rounded-full px-3 py-1 text-2xs text-faint transition hover:text-secondary"
                }
              >
                {filter.label}
              </Link>
            ))}
          </div>
        }
      />

      {data.items.length === 0 ? (
        <EmptyState
          title="No decisions yet"
          description="Run the decision process over an opportunity, and every stage of it will be recorded here."
          action={
            <Link href="/opportunities" className="text-xs text-accent hover:underline">
              Open the opportunity list
            </Link>
          }
        />
      ) : (
        <Card flush>
          <Table>
            <thead>
              <tr>
                <Th className="w-[110px]">Outcome</Th>
                <Th>Reason</Th>
                <Th>Stages</Th>
                <Th align="right">Committed</Th>
                <Th align="right">Max price</Th>
                <Th>Policy</Th>
                <Th align="right">When</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <Tr key={row.id}>
                  <Td>
                    <OutcomeBadge outcome={row.outcome} />
                  </Td>
                  <Td>
                    <Link
                      href={`/autonomy/decisions/${row.id}`}
                      className="block max-w-[420px] text-secondary transition hover:text-accent"
                    >
                      {row.reason}
                    </Link>
                    {row.reason_code && (
                      <div className="mt-0.5 font-mono text-2xs text-faint">
                        {row.reason_code}
                      </div>
                    )}
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      {Object.entries(row.stage_verdicts).map(([stage, verdict]) => (
                        <VerdictDot key={stage} verdict={verdict} label={stage} />
                      ))}
                    </div>
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    <Value>{row.capital_committed && money(row.capital_committed)}</Value>
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    <Value>{row.max_unit_price && money(row.max_unit_price)}</Value>
                  </Td>
                  <Td className="text-2xs text-faint">
                    {row.policy_version} · L{row.autonomy_level} · {row.execution_mode}
                  </Td>
                  <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                    {relativeDate(row.created_at)}
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
