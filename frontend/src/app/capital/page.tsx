"use client";

import { useState } from "react";

import { Card, ErrorState, Stat, Table, Td, Th, Value } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { money, percent } from "@/lib/format";

interface Allocation {
  opportunity_id: string;
  title: string;
  units: number;
  unit_cost: string;
  capital: string;
  expected_profit: string;
  expected_roi: string | null;
  risk_level: string;
  score: string;
  brand: string | null;
  category: string | null;
  limited_by: string;
}

interface Plan {
  allocations: Allocation[];
  excluded: { opportunity_id: string; title: string; reason: string }[];
  allocated_capital: string;
  unallocated_capital: string;
  expected_profit: string;
  expected_roi: string | null;
  position_count: number;
  candidates_considered: number;
  notes: string[];
}

/**
 * The capital simulator (spec §29). Constraints are all visible and editable,
 * and every excluded candidate is shown with the reason it was excluded, so the
 * plan can be argued with rather than merely accepted.
 */
export default function CapitalPage() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    available_capital: "10000",
    max_position_pct: "0.25",
    min_roi: "0.20",
    min_score: "50",
    max_risk_level: "medium",
    max_positions: "",
    max_brand_pct: "0.40",
    allow_review: false,
  });

  async function simulate() {
    setBusy(true);
    setError(null);
    try {
      const response = (await endpoints.simulateCapital({
        available_capital: form.available_capital,
        max_position_pct: form.max_position_pct,
        min_roi: form.min_roi,
        min_score: form.min_score,
        max_risk_level: form.max_risk_level,
        max_brand_pct: form.max_brand_pct,
        max_positions: form.max_positions ? Number(form.max_positions) : null,
        allowed_recommendations: form.allow_review ? ["buy", "review"] : ["buy"],
      })) as unknown as Plan;
      setPlan(response);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Capital</h1>
        <p className="mt-1 text-sm text-muted">
          Allocate capital across the analysed opportunities under explicit
          constraints. Ranking is by risk-adjusted profit per dollar.
        </p>
      </div>

      <Card title="Constraints">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <NumberField
            label="Available capital"
            value={form.available_capital}
            onChange={(value) => setForm({ ...form, available_capital: value })}
          />
          <NumberField
            label="Max position share"
            value={form.max_position_pct}
            step="0.05"
            onChange={(value) => setForm({ ...form, max_position_pct: value })}
          />
          <NumberField
            label="Minimum ROI"
            value={form.min_roi}
            step="0.05"
            onChange={(value) => setForm({ ...form, min_roi: value })}
          />
          <NumberField
            label="Minimum score"
            value={form.min_score}
            step="5"
            onChange={(value) => setForm({ ...form, min_score: value })}
          />
          <label className="block">
            <span className="text-2xs uppercase tracking-wide text-muted">Max risk</span>
            <select
              value={form.max_risk_level}
              onChange={(event) => setForm({ ...form, max_risk_level: event.target.value })}
              className="mt-1 w-full rounded border border-border bg-canvas px-2 py-1.5 text-sm outline-none focus:border-accent"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
          <NumberField
            label="Max brand share"
            value={form.max_brand_pct}
            step="0.05"
            onChange={(value) => setForm({ ...form, max_brand_pct: value })}
          />
          <NumberField
            label="Max positions"
            value={form.max_positions}
            placeholder="no limit"
            onChange={(value) => setForm({ ...form, max_positions: value })}
          />
          <label className="flex items-end gap-2 pb-1.5">
            <input
              type="checkbox"
              checked={form.allow_review}
              onChange={(event) => setForm({ ...form, allow_review: event.target.checked })}
              className="rounded border-border"
            />
            <span className="text-xs text-muted">Include review candidates</span>
          </label>
        </div>
        <button
          type="button"
          onClick={() => void simulate()}
          disabled={busy}
          className="mt-4 rounded border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent transition hover:bg-accent/20 disabled:opacity-50"
        >
          {busy ? "Allocating" : "Run simulation"}
        </button>
      </Card>

      {error && <ErrorState message={error} />}

      {plan && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Allocated" value={money(plan.allocated_capital)} />
            <Stat
              label="Unallocated"
              value={money(plan.unallocated_capital)}
              tone="muted"
            />
            <Stat
              label="Expected profit"
              value={money(plan.expected_profit)}
              tone="buy"
            />
            <Stat
              label="Expected ROI"
              value={<Value>{percent(plan.expected_roi)}</Value>}
              hint={`${plan.position_count} position(s) from ${plan.candidates_considered} candidate(s)`}
            />
          </div>

          {plan.notes.length > 0 && (
            <Card>
              <ul className="space-y-1 text-xs text-muted">
                {plan.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Card>
          )}

          {plan.allocations.length > 0 && (
            <Card title="Allocations">
              <Table>
                <thead>
                  <tr>
                    <Th>Product</Th>
                    <Th align="right">Units</Th>
                    <Th align="right">Unit cost</Th>
                    <Th align="right">Capital</Th>
                    <Th align="right">Expected profit</Th>
                    <Th align="right">ROI</Th>
                    <Th>Risk</Th>
                    <Th>Limited by</Th>
                  </tr>
                </thead>
                <tbody>
                  {plan.allocations.map((row) => (
                    <tr key={row.opportunity_id}>
                      <Td>
                        <a
                          href={`/opportunities/${row.opportunity_id}`}
                          className="text-accent hover:underline"
                        >
                          <span className="block max-w-xs truncate">{row.title}</span>
                        </a>
                      </Td>
                      <Td align="right" numeric>
                        {row.units}
                      </Td>
                      <Td align="right" numeric>
                        {money(row.unit_cost)}
                      </Td>
                      <Td align="right" numeric>
                        {money(row.capital)}
                      </Td>
                      <Td align="right" numeric className="text-buy">
                        {money(row.expected_profit)}
                      </Td>
                      <Td align="right" numeric>
                        <Value>{percent(row.expected_roi)}</Value>
                      </Td>
                      <Td className="capitalize text-muted">{row.risk_level}</Td>
                      <Td className="text-2xs text-muted">{row.limited_by}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}

          {plan.excluded.length > 0 && (
            <Card
              title="Excluded"
              subtitle="Every candidate that did not make the plan, and why"
            >
              <Table>
                <thead>
                  <tr>
                    <Th>Product</Th>
                    <Th>Reason</Th>
                  </tr>
                </thead>
                <tbody>
                  {plan.excluded.map((row) => (
                    <tr key={row.opportunity_id}>
                      <Td>
                        <span className="block max-w-xs truncate">{row.title}</span>
                      </Td>
                      <Td className="text-muted">{row.reason}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step = "1",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  step?: string;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-2xs uppercase tracking-wide text-muted">{label}</span>
      <input
        type="number"
        step={step}
        min="0"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded border border-border bg-canvas px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
    </label>
  );
}
