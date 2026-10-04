"use client";

/**
 * The controls that change what the system may do with money.
 *
 * Every one of them confirms first, states what it is about to authorise in
 * money terms, and refreshes the server view afterwards rather than guessing at
 * the new state. Turning autonomy on is the most consequential action in the
 * product; it should not be possible to do it by accident or by a default.
 */

import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Button, ErrorState } from "@/components/ui";
import { autonomyApi, type AutonomyOverview, type ExecutionMode } from "@/lib/api";
import { money } from "@/lib/format";

function useAction() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

/** Authorise, or withdraw, autonomous capital. */
export function AutonomySwitch({ overview }: { overview: AutonomyOverview }) {
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState(
    Math.max(3, overview.next_level?.target_level ?? 3),
  );
  const [capital, setCapital] = useState(
    overview.next_level?.target_capital_limit ?? "100",
  );
  const [mode, setMode] = useState<ExecutionMode>("shadow");
  const [acknowledge, setAcknowledge] = useState(false);

  const eligible = overview.next_level?.eligible ?? false;
  const active = overview.level.deploys_capital;

  if (active) {
    return (
      <div className="space-y-2">
        {error && <ErrorState message={error} />}
        <Button
          tone="pass"
          disabled={busy}
          onClick={() => {
            if (
              !window.confirm(
                "Withdraw autonomous capital and return to human approval? " +
                  "Existing positions are kept and still tracked.",
              )
            ) {
              return;
            }
            void run(() => autonomyApi.disable());
          }}
        >
          {busy ? "Working" : "Stop autonomy"}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && <ErrorState message={error} />}
      {!open ? (
        <Button tone="accent" onClick={() => setOpen(true)}>
          Authorise autonomous capital
        </Button>
      ) : (
        <div className="space-y-3 rounded-lg border border-accent/30 bg-accent/[0.06] px-4 py-4">
          <p className="text-xs leading-relaxed text-secondary">
            This authorises Spreadline to commit capital on its own, within the policy.
            Shadow mode records positions against real market data without buying
            anything, which is the honest place to start.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block">
              <span className="label">Level</span>
              <select
                value={level}
                onChange={(event) => setLevel(Number(event.target.value))}
                className="mt-1 w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
              >
                {[3, 4, 5, 6, 7].map((value) => (
                  <option key={value} value={value}>
                    Level {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="label">Capital limit</span>
              <input
                value={capital}
                onChange={(event) => setCapital(event.target.value)}
                inputMode="decimal"
                className="mt-1 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
              />
            </label>
            <label className="block">
              <span className="label">Mode</span>
              <select
                value={mode}
                onChange={(event) => setMode(event.target.value as ExecutionMode)}
                className="mt-1 w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
              >
                <option value="shadow">Shadow, no real money</option>
                <option value="live">Live, real capital</option>
              </select>
            </label>
          </div>

          {!eligible && (
            <label className="flex items-start gap-2 text-xs text-secondary">
              <input
                type="checkbox"
                checked={acknowledge}
                onChange={(event) => setAcknowledge(event.target.checked)}
                className="mt-0.5"
              />
              <span>
                The system has not met the criteria for this level. I am authorising it
                anyway.
              </span>
            </label>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              tone="accent"
              disabled={busy || (!eligible && !acknowledge)}
              onClick={() => {
                const label =
                  mode === "live"
                    ? `Authorise ${money(capital)} of REAL capital at level ${level}?`
                    : `Authorise ${money(capital)} of shadow capital at level ${level}? Nothing is bought.`;
                if (!window.confirm(label)) return;
                void run(() =>
                  autonomyApi.enable({
                    level,
                    capital_limit: capital,
                    execution_mode: mode,
                    acknowledge_not_eligible: acknowledge,
                  }),
                );
              }}
            >
              {busy ? "Working" : "Authorise"}
            </Button>
            <Button onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Stop or resume autonomous activity immediately. */
export function EmergencyStop({ active, reason }: { active: boolean; reason: string | null }) {
  const { busy, error, run } = useAction();

  return (
    <div className="space-y-2">
      {error && <ErrorState message={error} />}
      {active ? (
        <>
          <p className="text-xs leading-relaxed text-pass">
            Autonomous deployment is stopped. {reason}
          </p>
          <p className="text-2xs leading-relaxed text-faint">
            Existing positions are still held and still tracked. What stopped is new
            capital.
          </p>
          <Button
            disabled={busy}
            onClick={() => {
              const note = window.prompt("Reason for resuming?", "Reviewed and cleared");
              if (!note) return;
              void run(() => autonomyApi.emergencyStop({ active: false, reason: note }));
            }}
          >
            {busy ? "Working" : "Resume autonomy"}
          </Button>
        </>
      ) : (
        <Button
          tone="pass"
          disabled={busy}
          onClick={() => {
            const note = window.prompt(
              "Stop all autonomous capital deployment. Reason?",
              "Manual stop",
            );
            if (!note) return;
            void run(() => autonomyApi.emergencyStop({ active: true, reason: note }));
          }}
        >
          {busy ? "Working" : "Stop autonomous activity"}
        </Button>
      )}
    </div>
  );
}

/** Clear one capital circuit breaker. Always a human action. */
export function ResetBreaker({ id, label }: { id: string; label: string }) {
  const { busy, error, run } = useAction();
  return (
    <span className="inline-flex items-center gap-2">
      {error && <span className="text-2xs text-pass">{error}</span>}
      <Button
        size="small"
        disabled={busy}
        onClick={() => {
          if (!window.confirm(`Reset "${label}"? Look at what tripped it first.`)) return;
          void run(() => autonomyApi.resetBreaker(id));
        }}
      >
        {busy ? "Working" : "Reset"}
      </Button>
    </span>
  );
}

/** Run the decision process over one opportunity. */
export function RunDecision({ opportunityId }: { opportunityId: string }) {
  const { busy, error, run } = useAction();
  const [result, setResult] = useState<ReactNode>(null);

  return (
    <div className="space-y-2">
      {error && <ErrorState message={error} />}
      <Button
        tone="accent"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            const body = await autonomyApi.runDecision({ opportunity_id: opportunityId });
            setResult(
              <span className="text-xs text-secondary">
                {body.decision.outcome}: {body.decision.reason}
              </span>,
            );
            return body;
          })
        }
      >
        {busy ? "Running" : "Run the decision process"}
      </Button>
      {result}
    </div>
  );
}
