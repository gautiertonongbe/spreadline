"use client";

/**
 * Controls for the observation universe.
 *
 * Adding a listing is a standing instruction to keep observing it, so the form
 * takes the cadence as well as the identifier. Removing one deactivates it and
 * keeps everything already observed: a gap in a history should be explainable
 * by a row saying when observation stopped, not by a deletion.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, ErrorState } from "@/components/ui";
import { historyApi } from "@/lib/api";

export function TrackListingForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [marketplace, setMarketplace] = useState("amazon");
  const [externalId, setExternalId] = useState("");
  const [priority, setPriority] = useState("0");
  const [interval, setInterval] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await historyApi.track({
        marketplace,
        external_id: externalId.trim(),
        priority: Number(priority) || 0,
        refresh_interval_seconds: interval ? Number(interval) : null,
      });
      setExternalId("");
      setOpen(false);
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <Button tone="accent" onClick={() => setOpen(true)}>
        Observe a listing
      </Button>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface px-4 py-4 shadow-card">
      {error && <ErrorState message={error} />}
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="label">Marketplace</span>
          <select
            value={marketplace}
            onChange={(event) => setMarketplace(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
          >
            {["amazon", "walmart", "ebay", "bestbuy"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="block sm:col-span-2">
          <span className="label">Listing id</span>
          <input
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
            placeholder="B09XS7JWHH"
            className="mt-1 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
        <label className="block">
          <span className="label">Priority</span>
          <input
            value={priority}
            inputMode="numeric"
            onChange={(event) => setPriority(event.target.value)}
            className="numeric mt-1 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
      </div>
      <label className="block sm:w-[280px]">
        <span className="label">Poll every (seconds)</span>
        <input
          value={interval}
          inputMode="numeric"
          placeholder="use the default freshness policy"
          onChange={(event) => setInterval(event.target.value)}
          className="numeric mt-1 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary"
        />
      </label>
      <p className="text-2xs leading-relaxed text-faint">
        Polling faster than the freshness policy considers stale spends a provider call to
        learn nothing. Leave the cadence blank unless you mean to override it.
      </p>
      <div className="flex gap-2">
        <Button
          tone="accent"
          disabled={busy || !externalId.trim()}
          onClick={() => void submit()}
        >
          {busy ? "Working" : "Start observing"}
        </Button>
        <Button onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </div>
  );
}

export function UntrackButton({ id, label }: { id: string; label: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  return (
    <Button
      size="small"
      disabled={busy}
      onClick={async () => {
        if (
          !window.confirm(
            `Stop observing ${label}? Everything already observed is kept.`,
          )
        ) {
          return;
        }
        setBusy(true);
        try {
          await historyApi.untrack(id);
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Working" : "Stop"}
    </Button>
  );
}

export function RefreshUniverse() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          setResult(null);
          try {
            const body = await historyApi.refresh();
            setResult(
              `Polled ${body.polled}, wrote ${body.observations} observation(s), ` +
                `${body.failures} failure(s).`,
            );
            router.refresh();
          } catch (caught) {
            setError((caught as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Polling" : "Run one pass now"}
      </Button>
      {result && <span className="text-2xs text-muted">{result}</span>}
      {error && <span className="text-2xs text-pass">{error}</span>}
    </div>
  );
}
