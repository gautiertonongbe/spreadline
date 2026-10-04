"use client";

/**
 * The replay, and what it is allowed to claim.
 *
 * The ordering on this page is deliberate and is the opposite of every
 * backtesting tool that sells a strategy: the caveats sit above the return, not
 * folded away beneath it. A replay is the most persuasive artefact the product
 * can produce and the easiest one to misread, so the assumptions that produced
 * the number are part of the number.
 *
 * Running it is a POST because it is a computation over a window the person
 * chooses, not a stored report. Nothing here writes anything: no position, no
 * decision, no policy change.
 */

import { useState } from "react";

import { Button, Card, ErrorState, Note, Stat, Value } from "@/components/ui";
import { autonomyApi, type BacktestResult } from "@/lib/api";
import { date, money, percent, signedMoney } from "@/lib/format";

const WINDOWS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 180, label: "180 days" },
  { days: 365, label: "1 year" },
];

export function BacktestRunner({ defaultCapital }: { defaultCapital: string }) {
  const [days, setDays] = useState(90);
  const [capital, setCapital] = useState(defaultCapital);
  const [stepDays, setStepDays] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await autonomyApi.backtest({
          days,
          starting_capital: capital.trim() || undefined,
          step_days: Number(stepDays) || 1,
        }),
      );
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <Card
        title="Replay the active policy"
        subtitle="Only observations recorded on or before each simulated day are visible to the replay."
      >
        <div className="grid gap-4 lg:grid-cols-[1fr_200px_160px]">
          <div>
            <span className="label">Window</span>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {WINDOWS.map((option) => (
                <button
                  key={option.days}
                  type="button"
                  onClick={() => setDays(option.days)}
                  className={
                    "rounded border px-3 py-[7px] text-xs transition " +
                    (days === option.days
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-border bg-raised text-muted hover:text-secondary")
                  }
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <label className="block">
            <span className="label">Capital to replay</span>
            <input
              value={capital}
              inputMode="decimal"
              onChange={(event) => setCapital(event.target.value)}
              className="numeric mt-1.5 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
            />
          </label>
          <label className="block">
            <span className="label">Days per step</span>
            <input
              value={stepDays}
              inputMode="numeric"
              onChange={(event) => setStepDays(event.target.value)}
              className="numeric mt-1.5 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
            />
          </label>
        </div>
        <p className="mt-3 text-2xs leading-relaxed text-faint">
          The capital figure is the one being tested, not the one authorised: asking what
          $500 would have done is the question you ask before authorising $500. A step
          larger than a day is faster and will miss prices that only existed for a day.
        </p>
        <div className="mt-4 flex items-center gap-2">
          <Button tone="accent" disabled={busy} onClick={() => void run()}>
            {busy ? "Replaying" : "Run the replay"}
          </Button>
          {result && (
            <span className="text-2xs text-faint">
              Policy version {result.policy_version}
            </span>
          )}
        </div>
        {error && (
          <div className="mt-3">
            <ErrorState message={`The replay did not run. ${error}`} />
          </div>
        )}
      </Card>

      {result && <BacktestReport result={result} />}
    </div>
  );
}

function BacktestReport({ result }: { result: BacktestResult }) {
  const { results } = result;
  const nothingBought = results.positions_opened === 0;
  const fixtureHeavy = Number(result.evidence.simulated_share) > 0;

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-border bg-surface px-6 py-5 shadow-card">
        <div className="label">What the replay found</div>
        <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-secondary">
          {result.summary}
        </p>
        <p className="mt-3 text-2xs text-faint">
          {date(result.window.from)} to {date(result.window.to)} · {result.window.days} days
          · exit rule {result.exit_rule.replace(/_/g, " ")}
        </p>
      </section>

      {/* The caveats come first. A return figure read without its selling
          assumption is the thing this page exists to prevent. */}
      <Note tone={fixtureHeavy ? "warning" : "neutral"}>
        <div className="font-medium">What this does not prove</div>
        <ul className="mt-1.5 space-y-1">
          {result.caveats.map((caveat) => (
            <li key={caveat} className="flex gap-2">
              <span className="text-faint">·</span>
              <span>{caveat}</span>
            </li>
          ))}
        </ul>
      </Note>

      {nothingBought ? (
        <Card title="Nothing was bought" subtitle="Which is a result, not a failure">
          <p className="text-[0.875rem] leading-relaxed text-secondary">
            The policy refused every candidate it saw over this window. A policy that
            deploys nothing is doing its job if nothing cleared it; the refusals below say
            which limit did the refusing.
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Realised profit"
            value={<Value>{signedMoney(results.realized_profit)}</Value>}
            tone={Number(results.realized_profit) >= 0 ? "buy" : "pass"}
            hint={`on ${money(results.capital_committed)} committed`}
          />
          <Stat
            label="Return"
            value={<Value>{percent(results.realized_roi)}</Value>}
            hint="on capital that actually closed"
          />
          <Stat
            label="Positions that made money"
            value={<Value>{percent(results.win_rate, 0)}</Value>}
            hint={`${results.positions_closed} closed of ${results.positions_opened}`}
          />
          <Stat
            label="Worst single loss"
            value={<Value>{percent(results.worst_trade, 0)}</Value>}
            hint="of the capital in that position"
          />
        </div>
      )}

      {results.prediction_accuracy !== null && (
        <Card
          title="How close the predictions were"
          subtitle="The replayed profit against what the analysis said it would be"
        >
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <span className="numeric display text-2xl text-primary">
              {percent(results.prediction_accuracy, 0)}
            </span>
            <span className="max-w-xl text-xs leading-relaxed text-muted">
              Predicted profit against replayed profit, averaged across closed positions.
              It measures the analysis, not the market: a position that sold exactly at
              plan scores perfectly whether or not the plan was ambitious.
            </span>
          </div>
        </Card>
      )}

      {result.trades.length > 0 && (
        <Card
          title="Every simulated position"
          subtitle="Opened and closed by the rules above, in the order the replay reached them"
          flush
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hairline text-2xs text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-normal">Product</th>
                  <th className="px-3 py-2.5 text-right font-normal">Bought at</th>
                  <th className="px-3 py-2.5 text-right font-normal">Planned sale</th>
                  <th className="px-3 py-2.5 text-right font-normal">Sold at</th>
                  <th className="px-3 py-2.5 text-right font-normal">Qty</th>
                  <th className="px-3 py-2.5 text-right font-normal">Expected</th>
                  <th className="px-3 py-2.5 text-right font-normal">Replayed</th>
                  <th className="px-3 py-2.5 text-right font-normal">Days</th>
                  <th className="px-5 py-2.5 font-normal">How it ended</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((trade) => {
                  const realised = trade.realized_profit;
                  return (
                    <tr
                      key={`${trade.opportunity_id}-${trade.opened_at}`}
                      className="border-b border-hairline last:border-0"
                    >
                      <td className="max-w-[220px] truncate px-5 py-2.5 text-secondary">
                        {trade.title}
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-secondary">
                        {money(trade.entry_price)}
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-muted">
                        {money(trade.expected_sale_price)}
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-secondary">
                        <Value>{trade.exit_price ? money(trade.exit_price) : null}</Value>
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-muted">
                        {trade.quantity}
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-muted">
                        {money(trade.expected_profit)}
                      </td>
                      <td
                        className={
                          "numeric px-3 py-2.5 text-right " +
                          (realised === null
                            ? "text-faint"
                            : Number(realised) >= 0
                              ? "text-buy"
                              : "text-pass")
                        }
                      >
                        <Value>{realised === null ? null : signedMoney(realised)}</Value>
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-muted">
                        {trade.is_closed ? trade.days_held : ""}
                      </td>
                      <td className="px-5 py-2.5 text-muted">{trade.exit_reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {result.refusals.length > 0 && (
        <Card
          title="What it refused, and why"
          subtitle="One row per candidate and cause, most persistent first"
          flush
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hairline text-2xs text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-normal">Product</th>
                  <th className="px-3 py-2.5 font-normal">Limit that refused it</th>
                  <th className="px-3 py-2.5 text-right font-normal">Days</th>
                  <th className="px-5 py-2.5 font-normal">On the last of them</th>
                </tr>
              </thead>
              <tbody>
                {result.refusals.map((refusal) => (
                  <tr
                    key={`${refusal.opportunity_id}-${refusal.reason_code}`}
                    className="border-b border-hairline last:border-0"
                  >
                    <td className="max-w-[240px] truncate px-5 py-2.5 text-secondary">
                      {refusal.title}
                    </td>
                    <td className="px-3 py-2.5 text-secondary">
                      {refusal.reason_code.toLowerCase().replace(/_/g, " ")}
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-muted">
                      {refusal.days}
                    </td>
                    <td className="px-5 py-2.5 text-muted">
                      <span className="text-faint">{date(refusal.last_at)}</span>{" "}
                      {refusal.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {result.evidence.skipped.length > 0 && (
        <Card
          title="Candidates the replay could not judge"
          subtitle="Named rather than counted, so a thin result is not mistaken for a negative one"
        >
          <div className="space-y-1.5">
            {result.evidence.skipped.map((entry) => (
              <div
                key={entry.opportunity_id}
                className="flex flex-wrap items-baseline gap-x-3 text-2xs"
              >
                <span className="text-secondary">{entry.title}</span>
                <span className="text-faint">{entry.reason}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat
          label="Observations behind this"
          value={result.evidence.observations_used.toLocaleString()}
        />
        <Stat
          label="Fixture rather than market"
          value={percent(result.evidence.simulated_share, 0)}
          tone={fixtureHeavy ? "review" : "muted"}
          hint={fixtureHeavy ? "a replay over fixtures measures the fixtures" : undefined}
        />
        <Stat label="Capital replayed" value={money(result.starting_capital)} />
      </div>
    </div>
  );
}
