"use client";

/**
 * Acting on a plan, and changing the budget it was built for.
 *
 * Committing is the single most consequential button on this page, so it says
 * in money terms what it is about to do before it does it, and it is absent
 * rather than merely disabled when the policy does not allow it: a greyed-out
 * button invites a person to go looking for the switch that enables it, and the
 * blocker is more useful than the button.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, ErrorState, Note } from "@/components/ui";
import { autonomyApi, type AllocationCommit, type AllocationPlan } from "@/lib/api";
import { money } from "@/lib/format";

export function BudgetControl({ budget }: { budget: string | null }) {
  const router = useRouter();
  const [value, setValue] = useState(budget ?? "");

  const apply = () => {
    const trimmed = value.trim();
    router.push(trimmed ? `/autonomy/allocation?budget=${trimmed}` : "/autonomy/allocation");
  };

  return (
    <div className="flex items-end gap-2">
      <label className="block">
        <span className="label">Plan for</span>
        <input
          value={value}
          inputMode="decimal"
          placeholder="all available"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") apply();
          }}
          className="numeric mt-1.5 w-[150px] rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
        />
      </label>
      <Button onClick={apply}>Re-plan</Button>
    </div>
  );
}

export function CommitControl({ plan }: { plan: AllocationPlan }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AllocationCommit | null>(null);

  // What just happened outranks what is left to do. A successful commit empties
  // the plan it acted on, so returning early on an empty plan would erase the
  // only confirmation the person gets that anything happened at all.
  if (result) {
    return (
      <div className="space-y-2">
        <Note tone={result.refused > 0 ? "warning" : "neutral"}>{result.summary}</Note>
        <div className="space-y-1">
          {result.outcomes.map((outcome) => (
            <div
              key={outcome.opportunity_id}
              className="flex flex-wrap items-baseline gap-x-3 border-b border-hairline py-1.5 text-2xs last:border-0"
            >
              <span className={outcome.outcome === "authorized" ? "text-buy" : "text-pass"}>
                {outcome.outcome === "authorized" ? "Opened" : "Refused"}
              </span>
              <span className="text-secondary">{outcome.title}</span>
              <span className="numeric text-muted">{money(outcome.capital)}</span>
              <span className="text-faint">{outcome.reason}</span>
            </div>
          ))}
        </div>
        <Link href="/autonomy/positions" className="inline-block text-xs text-accent hover:underline">
          See the positions
        </Link>
      </div>
    );
  }

  if (!plan.lines.length) return null;

  if (!plan.may_commit) {
    return (
      <Note tone="warning">
        This plan cannot be acted on. {plan.blocked_reason} It is still worth reading: it
        is what the system would do with the limits as they stand.
      </Note>
    );
  }

  const commit = async () => {
    const lines = plan.lines.length;
    if (
      !window.confirm(
        `Open ${lines} position(s) committing ${money(plan.capital.allocated)} in ` +
          `${plan.execution_mode} mode? Each line is checked again by the decision ` +
          "engine on the way through, and the retail order stays a human action.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(
        await autonomyApi.commitAllocation({
          // Pinned to what is on screen. If the plan has changed since it was
          // read, the commit is refused rather than acting on a different one.
          expect_capital: plan.capital.allocated,
        }),
      );
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      {error && <ErrorState message={error} />}
      <Button tone="buy" disabled={busy} onClick={() => void commit()}>
        {busy ? "Opening positions" : `Commit ${money(plan.capital.allocated)}`}
      </Button>
    </div>
  );
}
