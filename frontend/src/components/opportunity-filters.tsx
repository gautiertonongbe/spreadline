"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { clsx } from "clsx";

import { Button } from "@/components/ui";

/**
 * Filters for the opportunity table.
 *
 * State lives in the URL, not in the component. That means a filtered view is a
 * link: it can be bookmarked, shared with someone else, and reloaded without
 * losing where you were. The page itself stays a server component and refetches
 * on every change.
 *
 * Every control maps to a filter the API already implements, so nothing here is
 * filtering client side over a truncated page of rows.
 */

const RECOMMENDATIONS = [
  { value: "buy", label: "Buy" },
  { value: "review", label: "Check" },
  { value: "pass", label: "Skip" },
];

const RISK_LEVELS = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "critical", label: "Critical" },
];

const STATUSES = [
  { value: "new", label: "New" },
  { value: "review", label: "Review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "purchased", label: "Purchased" },
  { value: "listed", label: "Listed" },
  { value: "sold", label: "Sold" },
  { value: "closed", label: "Closed" },
];

const MARKETPLACES = [
  { value: "", label: "Any marketplace" },
  { value: "amazon", label: "Amazon" },
  { value: "walmart", label: "Walmart" },
];

const SORTS = [
  { value: "score", label: "Score" },
  { value: "profit", label: "You make" },
  { value: "roi", label: "Return" },
  { value: "risk", label: "Risk" },
  { value: "analyzed_at", label: "Analysed" },
];

/** Presets encode the questions actually asked of an opportunity table. */
const PRESETS: { label: string; hint: string; params: Record<string, string | string[]> }[] = [
  {
    label: "Actionable",
    hint: "Worth buying, without much risk",
    params: { recommendation: ["buy"], risk_level: ["low", "medium"], sort: "score" },
  },
  {
    label: "Needs a look",
    hint: "Worth checking first, best score first",
    params: { recommendation: ["review"], sort: "score" },
  },
  {
    label: "Best return",
    hint: "Gives back at least 30% on your money",
    params: { min_roi: "0.30", sort: "roi" },
  },
  {
    label: "Biggest profit",
    hint: "Makes at least 20 on each item",
    params: { min_profit: "20", sort: "profit" },
  },
];

export interface FilterState {
  search: string;
  recommendation: string[];
  risk_level: string[];
  status: string[];
  min_score: string;
  min_roi: string;
  min_profit: string;
  marketplace: string;
  sort: string;
  descending: boolean;
}

export function readFilters(params: URLSearchParams): FilterState {
  return {
    search: params.get("search") ?? "",
    recommendation: params.getAll("recommendation"),
    risk_level: params.getAll("risk_level"),
    status: params.getAll("status"),
    min_score: params.get("min_score") ?? "",
    min_roi: params.get("min_roi") ?? "",
    min_profit: params.get("min_profit") ?? "",
    marketplace: params.get("marketplace") ?? "",
    sort: params.get("sort") ?? "score",
    descending: params.get("descending") !== "false",
  };
}

function countActive(state: FilterState): number {
  return (
    (state.search ? 1 : 0) +
    state.recommendation.length +
    state.risk_level.length +
    state.status.length +
    (state.min_score ? 1 : 0) +
    (state.min_roi ? 1 : 0) +
    (state.min_profit ? 1 : 0) +
    (state.marketplace ? 1 : 0)
  );
}

export function OpportunityFilters({ total }: { total: number }) {
  const router = useRouter();
  const params = useSearchParams();
  const state = readFilters(new URLSearchParams(params.toString()));
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState(state.search);

  // Keep the box in step when the URL changes from a preset or a cleared pill.
  useEffect(() => {
    setSearch(state.search);
  }, [state.search]);

  const push = useCallback(
    (next: Partial<Record<string, string | string[] | boolean | null>>) => {
      const query = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(next)) {
        query.delete(key);
        if (value === null || value === "" || value === undefined) continue;
        if (Array.isArray(value)) {
          value.forEach((item) => query.append(key, item));
        } else {
          query.set(key, String(value));
        }
      }
      router.push(`/opportunities?${query.toString()}`);
    },
    [params, router],
  );

  const toggleIn = (key: "recommendation" | "risk_level" | "status", value: string) => {
    const current = state[key];
    push({
      [key]: current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    });
  };

  // A preset replaces the filters, not the reader's chosen layout. Dropping
  // `view` here would silently throw a reviewer back to the full table every
  // time they clicked a preset.
  const preserved = () => {
    const query = new URLSearchParams();
    const view = params.get("view");
    if (view) query.set("view", view);
    return query;
  };

  const applyPreset = (preset: (typeof PRESETS)[number]) => {
    const query = preserved();
    for (const [key, value] of Object.entries(preset.params)) {
      if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
      else query.set(key, value);
    }
    router.push(`/opportunities?${query.toString()}`);
  };

  const clearAll = () => {
    const query = preserved().toString();
    router.push(query ? `/opportunities?${query}` : "/opportunities");
  };

  const active = countActive(state);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") push({ search: search.trim() || null });
              if (event.key === "Escape") {
                setSearch("");
                push({ search: null });
              }
            }}
            onBlur={() => {
              if (search.trim() !== state.search) push({ search: search.trim() || null });
            }}
            placeholder="Search by product title"
            className="w-full rounded border border-border bg-canvas px-3 py-[7px] pr-16 text-xs text-primary outline-none transition focus:border-accent/50"
          />
          {search && (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                push({ search: null });
              }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-2xs text-faint transition hover:text-primary"
            >
              clear
            </button>
          )}
        </div>

        <div className="flex items-center rounded border border-border bg-raised p-0.5">
          {RECOMMENDATIONS.map((item) => {
            const on = state.recommendation.includes(item.value);
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => toggleIn("recommendation", item.value)}
                className={clsx(
                  "rounded px-2.5 py-[5px] text-2xs font-medium transition",
                  on
                    ? item.value === "buy"
                      ? "bg-buy/15 text-buy"
                      : item.value === "review"
                        ? "bg-review/15 text-review"
                        : "bg-pass/15 text-pass"
                    : "text-muted hover:text-primary",
                )}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-1.5">
          <select
            value={state.sort}
            onChange={(event) => push({ sort: event.target.value })}
            className="rounded border border-border bg-canvas py-[7px] pl-2.5 text-2xs text-secondary outline-none transition focus:border-accent/50"
          >
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                Sort: {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => push({ descending: state.descending ? "false" : null })}
            title={state.descending ? "Highest first" : "Lowest first"}
            className="rounded border border-border bg-raised px-2 py-[6px] text-2xs text-muted transition hover:text-primary"
          >
            {state.descending ? "desc" : "asc"}
          </button>
        </div>

        <Button
          tone={open || active ? "accent" : "neutral"}
          onClick={() => setOpen((value) => !value)}
        >
          Filters{active ? ` (${active})` : ""}
        </Button>
      </div>

      {open && (
        <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-4">
            <FilterGroup label="Risk level">
              <div className="flex flex-wrap gap-1.5">
                {RISK_LEVELS.map((item) => (
                  <Chip
                    key={item.value}
                    label={item.label}
                    on={state.risk_level.includes(item.value)}
                    onClick={() => toggleIn("risk_level", item.value)}
                  />
                ))}
              </div>
            </FilterGroup>

            <FilterGroup label="Lifecycle status">
              <div className="flex flex-wrap gap-1.5">
                {STATUSES.map((item) => (
                  <Chip
                    key={item.value}
                    label={item.label}
                    on={state.status.includes(item.value)}
                    onClick={() => toggleIn("status", item.value)}
                  />
                ))}
              </div>
            </FilterGroup>

            <FilterGroup label="Thresholds">
              <div className="grid grid-cols-3 gap-2">
                <NumberInput
                  label="Score"
                  value={state.min_score}
                  placeholder="0"
                  onCommit={(value) => push({ min_score: value || null })}
                />
                <NumberInput
                  label="ROI"
                  value={state.min_roi}
                  placeholder="0.20"
                  step="0.05"
                  onCommit={(value) => push({ min_roi: value || null })}
                />
                <NumberInput
                  label="Profit"
                  value={state.min_profit}
                  placeholder="3"
                  onCommit={(value) => push({ min_profit: value || null })}
                />
              </div>
              <p className="mt-2 text-3xs leading-relaxed text-faint">
                ROI is a ratio: 0.20 is 20%.
              </p>
            </FilterGroup>

            <FilterGroup label="Marketplace">
              <select
                value={state.marketplace}
                onChange={(event) => push({ marketplace: event.target.value || null })}
                className="w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-secondary outline-none transition focus:border-accent/50"
              >
                {MARKETPLACES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-3xs leading-relaxed text-faint">
                Matches either side of the spread.
              </p>
            </FilterGroup>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
            <span className="label mr-1">Presets</span>
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                title={preset.hint}
                onClick={() => applyPreset(preset)}
                className="rounded border border-border bg-raised px-2.5 py-1 text-2xs text-secondary transition hover:border-accent/40 hover:text-accent"
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {active > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label mr-1">
            {total} match{total === 1 ? "" : "es"}
          </span>
          {state.search && (
            <Pill label={`title: ${state.search}`} onRemove={() => push({ search: null })} />
          )}
          {state.recommendation.map((value) => (
            <Pill
              key={`rec-${value}`}
              label={value}
              onRemove={() => toggleIn("recommendation", value)}
            />
          ))}
          {state.risk_level.map((value) => (
            <Pill
              key={`risk-${value}`}
              label={`risk: ${value}`}
              onRemove={() => toggleIn("risk_level", value)}
            />
          ))}
          {state.status.map((value) => (
            <Pill
              key={`status-${value}`}
              label={`status: ${value}`}
              onRemove={() => toggleIn("status", value)}
            />
          ))}
          {state.min_score && (
            <Pill
              label={`score >= ${state.min_score}`}
              onRemove={() => push({ min_score: null })}
            />
          )}
          {state.min_roi && (
            <Pill
              label={`ROI >= ${(Number(state.min_roi) * 100).toFixed(0)}%`}
              onRemove={() => push({ min_roi: null })}
            />
          )}
          {state.min_profit && (
            <Pill
              label={`profit >= ${state.min_profit}`}
              onRemove={() => push({ min_profit: null })}
            />
          )}
          {state.marketplace && (
            <Pill
              label={state.marketplace}
              onRemove={() => push({ marketplace: null })}
            />
          )}
          <button
            type="button"
            onClick={clearAll}
            className="ml-1 text-2xs text-faint underline-offset-2 transition hover:text-primary hover:underline"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="label mb-2">{label}</div>
      {children}
    </div>
  );
}

function Chip({
  label,
  on,
  onClick,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded border px-2 py-1 text-2xs transition",
        on
          ? "border-accent/40 bg-accent/10 text-accent"
          : "border-border bg-raised text-muted hover:text-primary",
      )}
    >
      {label}
    </button>
  );
}

function NumberInput({
  label,
  value,
  placeholder,
  step = "1",
  onCommit,
}: {
  label: string;
  value: string;
  placeholder?: string;
  step?: string;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <label className="block">
      <span className="mb-1 block text-3xs text-faint">{label}</span>
      <input
        type="number"
        min="0"
        step={step}
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onCommit(draft);
        }}
        className="numeric w-full rounded border border-border bg-canvas px-2 py-1.5 text-xs text-primary outline-none transition focus:border-accent/50"
      />
    </label>
  );
}

function Pill({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-border bg-raised py-1 pl-2 pr-1 text-2xs text-secondary">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        className="rounded px-1 text-faint transition hover:bg-overlay hover:text-primary"
      >
        x
      </button>
    </span>
  );
}
