"use client";

/**
 * Reporting what actually happened at the shop.
 *
 * The form defaults to the authorised quantity and the ceiling price, because
 * most of the time that is what happened and retyping it is friction. It does
 * not *enforce* them: the interesting reports are the ones that differ, and a
 * form that refused to record a price above the ceiling would simply mean the
 * purchase went unrecorded and every downstream number stayed wrong.
 *
 * Zero is a first-class answer. "The stock was gone" is an outcome, and
 * recording it releases the capital the position was holding.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, ErrorState, Note } from "@/components/ui";
import { executionApi, type ExecutionInstruction } from "@/lib/api";
import { money } from "@/lib/format";

export function RecordExecution({ instruction }: { instruction: ExecutionInstruction }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [quantity, setQuantity] = useState(String(instruction.quantity_authorized));
  const [price, setPrice] = useState(instruction.max_unit_price);
  const [reference, setReference] = useState("");
  const [shipping, setShipping] = useState("0");
  const [tax, setTax] = useState("0");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bought = Number(quantity) || 0;
  const overCeiling = Number(price) > Number(instruction.max_unit_price);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setOpen(false);
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit = () =>
    run(() =>
      executionApi.record(instruction.id, {
        quantity: bought,
        unit_price: bought > 0 ? price : undefined,
        order_reference: reference.trim() || undefined,
        shipping_cost: shipping || "0",
        tax: tax || "0",
        notes: notes.trim() || undefined,
      }),
    );

  const cancel = () => {
    const reason = window.prompt("Why is this not being bought?");
    if (!reason) return;
    void run(() => executionApi.cancel(instruction.id, { reason }));
  };

  if (!open) {
    return (
      <div className="flex flex-wrap gap-2">
        <Button tone="accent" size="small" onClick={() => setOpen(true)}>
          Record what I bought
        </Button>
        <Button size="small" onClick={cancel} disabled={busy}>
          Not buying this
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded border border-border bg-canvas px-4 py-3">
      {error && <ErrorState message={error} />}
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="label">Units bought</span>
          <input
            value={quantity}
            inputMode="numeric"
            onChange={(event) => setQuantity(event.target.value)}
            className="numeric mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
        <label className="block">
          <span className="label">Price paid each</span>
          <input
            value={price}
            inputMode="decimal"
            disabled={bought === 0}
            onChange={(event) => setPrice(event.target.value)}
            className="numeric mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary disabled:opacity-45"
          />
        </label>
        <label className="block">
          <span className="label">Shipping</span>
          <input
            value={shipping}
            inputMode="decimal"
            onChange={(event) => setShipping(event.target.value)}
            className="numeric mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
        <label className="block">
          <span className="label">Tax</span>
          <input
            value={tax}
            inputMode="decimal"
            onChange={(event) => setTax(event.target.value)}
            className="numeric mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="label">Order reference</span>
          <input
            value={reference}
            placeholder="the shop's order number"
            onChange={(event) => setReference(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
        <label className="block">
          <span className="label">Note</span>
          <input
            value={notes}
            placeholder={bought === 0 ? "why nothing was bought" : "anything worth recording"}
            onChange={(event) => setNotes(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-[7px] text-xs text-primary"
          />
        </label>
      </div>

      {bought === 0 && (
        <Note>
          Recording zero closes this out and releases the {money(instruction.capital_authorized)}{" "}
          the position was holding. That is the right answer when the stock was gone:
          capital the system believes is working and is not distorts every limit.
        </Note>
      )}
      {bought > 0 && overCeiling && (
        <Note tone="warning">
          {money(price)} is above the {money(instruction.max_unit_price)} ceiling this
          decision rested on. It will be recorded, and recorded as a departure: the
          position is then not the one that was underwritten.
        </Note>
      )}
      {bought > instruction.quantity_authorized && (
        <Note tone="warning">
          {bought} units is more than the {instruction.quantity_authorized} authorised. The
          limits did not approve the extra.
        </Note>
      )}

      <div className="flex gap-2">
        <Button tone="accent" disabled={busy} onClick={() => void submit()}>
          {busy ? "Recording" : bought === 0 ? "Record: nothing bought" : "Record purchase"}
        </Button>
        <Button onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
