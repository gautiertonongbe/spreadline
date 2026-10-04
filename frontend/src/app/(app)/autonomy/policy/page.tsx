import Link from "next/link";

import { LimitRow } from "@/components/autonomy-ui";
import { PolicyForm } from "@/components/policy-form";
import { Card, ErrorState, Note, PageHeader, Table, Td, Th, Tr } from "@/components/ui";
import { autonomyApi, type AutonomyPolicy, type PolicyVersion } from "@/lib/api";
import { date, money, percent } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * The investment policy.
 *
 * The human defines the policy; the system executes within it. That division
 * only holds if the policy a decision was made under can be read back exactly
 * as it was, so the version history is shown next to the editor rather than
 * hidden behind it.
 */
export default async function PolicyPage() {
  let data: { policy: AutonomyPolicy; history: PolicyVersion[] };
  try {
    data = await autonomyApi.policy();
  } catch (error) {
    return <ErrorState message={`Could not load the policy. ${(error as Error).message}`} />;
  }

  const { policy, history } = data;
  const switches: { label: string; on: boolean; detail: string }[] = [
    {
      label: "Level deploys capital",
      on: policy.autonomy_level >= 3,
      detail: `Level ${policy.autonomy_level}, ${policy.autonomy_level_label}`,
    },
    {
      label: "Human approval not required",
      on: !policy.require_human_approval,
      detail: policy.require_human_approval
        ? "A person approves every position"
        : "The system may open positions on its own",
    },
    {
      label: "No emergency stop",
      on: !policy.emergency_stop_active,
      detail: policy.emergency_stop_active
        ? (policy.emergency_stop_reason ?? "stopped")
        : "Not stopped",
    },
    {
      label: "Execution mode is not observe",
      on: policy.execution_mode !== "observe",
      detail: `${policy.execution_mode} mode`,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Policy and limits"
        description="You define the rules. Spreadline executes within them. Every change is a new version and the old one is kept."
        actions={
          <Link href="/autonomy" className="text-xs text-accent hover:underline">
            Back to autonomy
          </Link>
        }
      />

      <Card
        title={`Active policy ${policy.version}`}
        subtitle="Four independent switches have to agree before any capital moves"
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            {switches.map((item) => (
              <div key={item.label} className="flex items-start gap-2.5">
                <span
                  className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${
                    item.on ? "bg-buy" : "bg-faint"
                  }`}
                />
                <div className="min-w-0">
                  <div className="text-[0.8125rem] text-secondary">{item.label}</div>
                  <div className="text-2xs text-faint">{item.detail}</div>
                </div>
              </div>
            ))}
            <p className="pt-2 text-2xs leading-relaxed text-faint">
              They are separate on purpose. One of them being wrong should never be enough
              to move money.
            </p>
          </div>
          <div>
            <LimitRow label="Capital limit" value={money(policy.capital_limit)} />
            <LimitRow
              label="Maximum in one position"
              value={percent(policy.max_position_pct)}
              hint="of the capital limit"
            />
            <LimitRow label="Minimum return" value={percent(policy.minimum_roi)} />
            <LimitRow label="Minimum profit per item" value={money(policy.minimum_profit)} />
            <LimitRow label="Maximum risk" value={policy.maximum_risk} />
            <LimitRow
              label="Minimum match confidence"
              value={percent(policy.minimum_match_confidence)}
            />
          </div>
        </div>
      </Card>

      <PolicyForm policy={policy} />

      <Card
        title="Version history"
        subtitle="A decision made under an earlier version is still read under that version"
        flush
      >
        <Table>
          <thead>
            <tr>
              <Th>Version</Th>
              <Th>Level</Th>
              <Th align="right">Capital</Th>
              <Th>Mode</Th>
              <Th>Note</Th>
              <Th align="right">Written</Th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <Tr key={row.version}>
                <Td>
                  <span className={row.is_active ? "text-primary" : "text-muted"}>
                    {row.version}
                  </span>
                  {row.is_active && (
                    <span className="ml-2 text-3xs uppercase tracking-label text-buy">
                      active
                    </span>
                  )}
                </Td>
                <Td numeric className="text-muted">
                  {row.autonomy_level}
                </Td>
                <Td align="right" numeric className="text-secondary">
                  {money(row.capital_limit)}
                </Td>
                <Td className="text-muted">{row.execution_mode}</Td>
                <Td className="max-w-[320px] truncate text-2xs text-faint">{row.note}</Td>
                <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                  {date(row.created_at)}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <Note>
        Raising a limit here is a decision you are making. Nothing inside Spreadline calls
        this page: the system cannot widen its own mandate, and the gate that reports
        whether it has earned more never acts on its own answer.
      </Note>
    </div>
  );
}
