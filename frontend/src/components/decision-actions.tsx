"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { endpoints } from "@/lib/api";

/**
 * Approve, reject and hand-validation controls.
 *
 * The validation form is the personal testing workflow: it records what the
 * operator actually found, and deliberately leaves every field optional so an
 * unanswered question stays null rather than being guessed.
 */
export function DecisionActions({
  opportunityId,
  status,
}: {
  opportunityId: string;
  status: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showValidation, setShowValidation] = useState(false);

  async function decide(next: string) {
    setBusy(true);
    setError(null);
    try {
      await endpoints.decide(opportunityId, next);
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const canApprove = ["new", "review", "rejected"].includes(status);
  const canReject = ["new", "review", "approved"].includes(status);

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setShowValidation((open) => !open)}
          className="rounded border border-border bg-raised px-3 py-1.5 text-xs transition hover:border-muted"
        >
          {showValidation ? "Close" : "Record a hand check"}
        </button>
        {canReject && (
          <button
            type="button"
            disabled={busy}
            onClick={() => decide("rejected")}
            className="rounded border border-pass/30 bg-pass/10 px-3 py-1.5 text-xs text-pass transition hover:bg-pass/20 disabled:opacity-50"
          >
            Reject
          </button>
        )}
        {canApprove && (
          <button
            type="button"
            disabled={busy}
            onClick={() => decide("approved")}
            className="rounded border border-buy/30 bg-buy/10 px-3 py-1.5 text-xs text-buy transition hover:bg-buy/20 disabled:opacity-50"
          >
            Approve
          </button>
        )}
      </div>
      {error && <div className="text-2xs text-pass">{error}</div>}
      {showValidation && (
        <ValidationForm
          opportunityId={opportunityId}
          onDone={() => {
            setShowValidation(false);
            router.refresh();
          }}
        />
      )}
    </div>
  );
}

function ValidationForm({
  opportunityId,
  onDone,
}: {
  opportunityId: string;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(formData: FormData) {
    setBusy(true);
    setError(null);
    const tri = (name: string) => {
      const value = formData.get(name);
      return value === "" || value === null ? null : value === "yes";
    };
    const numeric = (name: string) => {
      const value = formData.get(name);
      return value === "" || value === null ? null : String(value);
    };
    try {
      await endpoints.validate(opportunityId, {
        actual_source_price: numeric("actual_source_price"),
        actual_target_price: numeric("actual_target_price"),
        match_confirmed: tri("match_confirmed"),
        profit_estimate_correct: tri("profit_estimate_correct"),
        would_buy: tri("would_buy"),
        notes: formData.get("notes") || null,
      });
      onDone();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      action={submit}
      className="w-80 space-y-3 rounded-lg border border-border bg-surface p-4 text-left"
    >
      <div className="text-xs text-muted">
        Record what you actually found. Leave anything you did not check blank: a
        blank answer is stored as unknown, not as a no.
      </div>
      <Field label="Actual source price" name="actual_source_price" />
      <Field label="Actual exit price" name="actual_target_price" />
      <Tri label="Match confirmed" name="match_confirmed" />
      <Tri label="Profit estimate correct" name="profit_estimate_correct" />
      <Tri label="Would you buy it" name="would_buy" />
      <label className="block">
        <span className="text-2xs uppercase tracking-wide text-muted">Notes</span>
        <textarea
          name="notes"
          rows={2}
          className="mt-1 w-full rounded border border-border bg-canvas px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
      </label>
      {error && <div className="text-2xs text-pass">{error}</div>}
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs text-accent transition hover:bg-accent/20 disabled:opacity-50"
      >
        {busy ? "Saving" : "Save validation"}
      </button>
    </form>
  );
}

function Field({ label, name }: { label: string; name: string }) {
  return (
    <label className="block">
      <span className="text-2xs uppercase tracking-wide text-muted">{label}</span>
      <input
        name={name}
        type="number"
        step="0.01"
        min="0"
        placeholder="leave blank if not checked"
        className="mt-1 w-full rounded border border-border bg-canvas px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
    </label>
  );
}

function Tri({ label, name }: { label: string; name: string }) {
  return (
    <label className="block">
      <span className="text-2xs uppercase tracking-wide text-muted">{label}</span>
      <select
        name={name}
        defaultValue=""
        className="mt-1 w-full rounded border border-border bg-canvas px-2 py-1.5 text-sm outline-none focus:border-accent"
      >
        <option value="">not checked</option>
        <option value="yes">yes</option>
        <option value="no">no</option>
      </select>
    </label>
  );
}
