"use client";

/**
 * The investment policy, edited.
 *
 * Every save writes a new version; the previous one is kept. The form submits
 * only the fields that actually changed, because "absent" and "set to null" are
 * different instructions to the API and collapsing them would silently clear
 * limits the operator never touched.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, ErrorState } from "@/components/ui";
import { autonomyApi, type AutonomyPolicy } from "@/lib/api";

interface FieldSpec {
  key: keyof AutonomyPolicy;
  label: string;
  hint: string;
  kind: "money" | "ratio" | "int" | "risk" | "bool";
}

const GROUPS: { title: string; blurb: string; fields: FieldSpec[] }[] = [
  {
    title: "Capital",
    blurb: "How much the system may commit, and how much of it in one place.",
    fields: [
      {
        key: "capital_limit",
        label: "Capital limit",
        hint: "The ceiling on capital it may commit on its own.",
        kind: "money",
      },
      {
        key: "max_position_pct",
        label: "Maximum share in one position",
        hint: "As a fraction of the capital limit. 0.15 is 15%.",
        kind: "ratio",
      },
      {
        key: "max_position_size",
        label: "Maximum position size",
        hint: "An absolute cap. The tighter of the two binds. Blank for none.",
        kind: "money",
      },
      {
        key: "max_loss_per_position",
        label: "Maximum loss per position",
        hint: "Blank for none.",
        kind: "money",
      },
      {
        key: "max_daily_deployment",
        label: "Maximum deployed per day",
        hint: "Blank for none.",
        kind: "money",
      },
    ],
  },
  {
    title: "What qualifies",
    blurb: "Every one of these is mandatory. One failure makes a candidate ineligible.",
    fields: [
      { key: "minimum_roi", label: "Minimum return", hint: "0.20 is 20%.", kind: "ratio" },
      {
        key: "minimum_profit",
        label: "Minimum profit per item",
        hint: "After every fee.",
        kind: "money",
      },
      {
        key: "maximum_risk",
        label: "Maximum risk",
        hint: "The overall level, driven by whichever category scored worst.",
        kind: "risk",
      },
      {
        key: "minimum_match_confidence",
        label: "Minimum match confidence",
        hint: "Buying the wrong item loses the position, not the margin.",
        kind: "ratio",
      },
      {
        key: "minimum_data_quality",
        label: "Minimum data quality",
        hint: "Out of 100.",
        kind: "ratio",
      },
      {
        key: "maximum_inventory_age_days",
        label: "Maximum inventory age",
        hint: "Days.",
        kind: "int",
      },
    ],
  },
  {
    title: "Concentration",
    blurb: "Shares of the capital limit allowed in one brand, category or marketplace.",
    fields: [
      { key: "maximum_brand_exposure", label: "Maximum in one brand", hint: "", kind: "ratio" },
      {
        key: "maximum_category_exposure",
        label: "Maximum in one category",
        hint: "",
        kind: "ratio",
      },
      {
        key: "maximum_marketplace_exposure",
        label: "Maximum in one marketplace",
        hint: "",
        kind: "ratio",
      },
    ],
  },
  {
    title: "Governance",
    blurb: "The switches that decide whether anything happens without a person.",
    fields: [
      {
        key: "require_human_approval",
        label: "Require human approval",
        hint: "On means no position is opened without a person, whatever the level.",
        kind: "bool",
      },
      {
        key: "emergency_stop_enabled",
        label: "Emergency stop available",
        hint: "",
        kind: "bool",
      },
    ],
  },
];

export function PolicyForm({ policy }: { policy: AutonomyPolicy }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      GROUPS.flatMap((group) => group.fields).map((field) => {
        const value = policy[field.key];
        return [field.key, value === null || value === undefined ? "" : String(value)];
      }),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const original = (field: FieldSpec) => {
    const value = policy[field.key];
    return value === null || value === undefined ? "" : String(value);
  };

  const changed = GROUPS.flatMap((group) => group.fields).filter(
    (field) => draft[field.key] !== original(field),
  );

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const body: Record<string, unknown> = {};
      for (const field of changed) {
        const raw = draft[field.key] ?? "";
        if (field.kind === "bool") {
          body[field.key] = raw === "true";
        } else if (raw === "") {
          body[field.key] = null;
        } else if (field.kind === "int") {
          body[field.key] = Number(raw);
        } else {
          body[field.key] = raw;
        }
      }
      const updated = await autonomyApi.updatePolicy(body);
      setSaved(`Saved as ${updated.version}. The previous version is kept.`);
      router.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      {error && <ErrorState message={error} />}
      {saved && (
        <div className="rounded border border-buy/25 bg-buy/10 px-3 py-2 text-xs text-buy">
          {saved}
        </div>
      )}

      {GROUPS.map((group) => (
        <section
          key={group.title}
          className="overflow-hidden rounded-lg border border-border bg-surface shadow-card"
        >
          <header className="border-b border-hairline bg-hairline-top px-5 py-3.5">
            <h2 className="display text-[0.9375rem] font-medium tracking-tight text-primary">
              {group.title}
            </h2>
            <p className="mt-1 text-xs text-muted">{group.blurb}</p>
          </header>
          <div className="divide-y divide-hairline">
            {group.fields.map((field) => (
              <div
                key={field.key}
                className="flex flex-wrap items-center justify-between gap-4 px-5 py-3"
              >
                <div className="min-w-0">
                  <div className="text-[0.8125rem] text-secondary">{field.label}</div>
                  {field.hint && <div className="text-2xs text-faint">{field.hint}</div>}
                </div>
                <div className="shrink-0">
                  {field.kind === "bool" ? (
                    <select
                      value={draft[field.key]}
                      onChange={(event) =>
                        setDraft({ ...draft, [field.key]: event.target.value })
                      }
                      className="w-[190px] rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
                    >
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  ) : field.kind === "risk" ? (
                    <select
                      value={draft[field.key]}
                      onChange={(event) =>
                        setDraft({ ...draft, [field.key]: event.target.value })
                      }
                      className="w-[190px] rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
                    >
                      {["low", "medium", "high", "critical"].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={draft[field.key]}
                      inputMode="decimal"
                      placeholder="none"
                      onChange={(event) =>
                        setDraft({ ...draft, [field.key]: event.target.value })
                      }
                      className="numeric w-[190px] rounded border border-border bg-canvas px-2.5 py-[7px] text-right text-xs text-primary"
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="flex flex-wrap items-center gap-3">
        <Button tone="accent" disabled={busy || changed.length === 0} onClick={() => void save()}>
          {busy
            ? "Saving"
            : changed.length === 0
              ? "No changes"
              : `Save ${changed.length} change(s) as a new version`}
        </Button>
        {changed.length > 0 && (
          <span className="text-2xs text-faint">
            {changed.map((field) => field.label).join(", ")}
          </span>
        )}
      </div>
    </div>
  );
}
