import Link from "next/link";

import {
  RefreshUniverse,
  TrackListingForm,
  UntrackButton,
} from "@/components/history-controls";
import {
  Card,
  EmptyState,
  ErrorState,
  Note,
  PageHeader,
  Stat,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui";
import { historyApi, type DatasetTotals, type TrackedListing } from "@/lib/api";
import { count, date, relativeDate, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

function share(part: number, whole: number): string {
  if (whole <= 0) return "0%";
  return `${((part / whole) * 100).toFixed(0)}%`;
}

/**
 * Spreadline's own observation history.
 *
 * The page leads with how much of the dataset is a real market record, because
 * that is the whole argument for owning it. A total that mixed real and
 * simulated observations would undermine exactly the claim it was meant to
 * support, so the split is the first thing on the screen rather than a footnote.
 */
export default async function HistoryPage() {
  let dataset: DatasetTotals;
  let universe: { items: TrackedListing[]; total: number; due_now: number };
  try {
    [dataset, universe] = await Promise.all([historyApi.dataset(), historyApi.universe()]);
  } catch (error) {
    return <ErrorState message={`Could not load the dataset. ${(error as Error).message}`} />;
  }

  const real = dataset.price_observations_real;
  const total = dataset.price_observations;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Observation history"
        description="Every price statistic in Spreadline is computed from observations it recorded itself. This is that dataset."
        actions={<TrackListingForm />}
      />

      <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-card">
        <div className="px-6 pb-5 pt-6">
          <div className="label">Price observations</div>
          <div className="display mt-2 text-[2rem] font-medium leading-none tracking-tight text-primary">
            {count(total)}
          </div>
          <p className="mt-3 max-w-2xl text-[0.8125rem] leading-relaxed text-secondary">
            {real === 0
              ? "None of it is a market record yet. Everything here came from the fixture providers, and it is marked as such at capture so it can never be read as something a market said."
              : `${count(real)} of them (${share(real, total)}) are real market observations. The rest came from fixtures and are marked as such.`}
          </p>
        </div>
        <div className="grid gap-px border-t border-border bg-border sm:grid-cols-3">
          {[
            {
              label: "Real market records",
              value: count(real),
              hint: "what a provider actually reported from a market",
            },
            {
              label: "Simulated",
              value: count(dataset.price_observations_simulated),
              hint: "fixture data, recorded as fixture data",
            },
            {
              label: "Observing since",
              value: dataset.earliest_observation_at
                ? relativeDate(dataset.earliest_observation_at)
                : "not started",
              hint: "the oldest observation on record",
            },
          ].map((item) => (
            <div key={item.label} className="bg-surface px-6 py-5">
              <div className="label">{item.label}</div>
              <div className="display mt-2 text-[1.5rem] font-medium leading-none tracking-tight text-primary">
                {item.value}
              </div>
              <div className="mt-2 text-2xs text-muted">{item.hint}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Demand observations"
          value={count(dataset.demand_observations)}
          hint={`${count(dataset.demand_observations_real)} real`}
        />
        <Stat
          label="Competition observations"
          value={count(dataset.competition_observations)}
          hint={`${count(dataset.competition_observations_real)} real`}
        />
        <Stat
          label="Under observation"
          value={dataset.tracked_listings_active}
          hint={`${universe.due_now} due now`}
        />
        <Stat
          label="Windows computed"
          value={dataset.windows_supported.length}
          hint={`${dataset.windows_supported.join(", ")} days`}
        />
      </div>

      <Card
        title="The universe"
        subtitle="Listings Spreadline keeps observing, and when each is next due"
        actions={<RefreshUniverse />}
        flush
      >
        {universe.items.length === 0 ? (
          <div className="px-5 py-8">
            <EmptyState
              title="Nothing under observation"
              description="Analysing a product puts it here automatically, or add a listing directly. The dataset is only worth owning if it kept observing after the question that prompted it."
              action={
                <Link href="/analyze" className="text-xs text-accent hover:underline">
                  Analyse a product
                </Link>
              }
            />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Listing</Th>
                <Th>Why</Th>
                <Th align="right">Priority</Th>
                <Th align="right">Every</Th>
                <Th align="right">Observations</Th>
                <Th align="right">Last polled</Th>
                <Th align="right">Next due</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {universe.items.map((row) => (
                <Tr key={row.id}>
                  <Td>
                    <div className="text-secondary">{titleCase(row.marketplace)}</div>
                    <div className="mt-0.5 font-mono text-2xs text-faint">
                      {row.external_id}
                    </div>
                  </Td>
                  <Td className="text-2xs text-muted">{row.reason}</Td>
                  <Td align="right" numeric className="text-muted">
                    {row.priority}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {Math.round(row.refresh_interval_seconds / 60)}m
                    {row.refresh_interval_is_default && (
                      <span className="ml-1.5 text-3xs text-faint">default</span>
                    )}
                  </Td>
                  <Td align="right" numeric className="text-secondary">
                    {count(row.observation_count)}
                  </Td>
                  <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                    {row.last_refreshed_at ? relativeDate(row.last_refreshed_at) : "never"}
                    {row.consecutive_failures > 0 && (
                      <div className="text-2xs text-pass">
                        {row.consecutive_failures} failure(s), backing off
                      </div>
                    )}
                  </Td>
                  <Td align="right" className="whitespace-nowrap text-2xs text-faint">
                    {!row.due_at || new Date(row.due_at) <= new Date()
                      ? "due now"
                      : date(row.due_at)}
                  </Td>
                  <Td align="right">
                    <UntrackButton
                      id={row.id}
                      label={`${row.marketplace} ${row.external_id}`}
                    />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Note>
        The universe is bounded on purpose. Spreadline observes what you are working on; it
        does not crawl a marketplace. A listing that keeps failing backs off geometrically
        rather than being retried every run, so one dead id costs one call a day rather
        than one call a pass.
      </Note>
    </div>
  );
}
