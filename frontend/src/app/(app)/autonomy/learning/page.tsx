import Link from "next/link";

import { Card, EmptyState, ErrorState, Note, PageHeader, Stat } from "@/components/ui";
import { autonomyApi, type LearningReport } from "@/lib/api";
import { money, percent } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Where the predictions are consistently wrong.
 *
 * The scorecard says how accurate the system has been. An average error is the
 * one number guaranteed to hide a pattern, so this page exists to show where
 * the error actually lives, and to say plainly when there is not enough behind
 * a segment to call one.
 */
export default async function LearningPage() {
  let report: LearningReport;
  try {
    report = await autonomyApi.learning();
  } catch (error) {
    return <ErrorState message={`Could not load the analysis. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="What it gets wrong"
        description="Prediction error, segmented. An average error hides a pattern: a category consistently overestimated and another underestimated average to nothing at all."
        actions={
          <Link href="/autonomy" className="text-xs text-accent hover:underline">
            Back to autonomy
          </Link>
        }
      />

      <section className="rounded-xl border border-border bg-surface px-6 py-5 shadow-card">
        <div className="label">Where this stands</div>
        <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-secondary">
          {report.summary}
        </p>
      </section>

      {report.findings.length === 0 ? (
        <EmptyState
          title={
            report.observations === 0
              ? "Nothing measured yet"
              : "No pattern strong enough to name"
          }
          description={
            report.observations === 0
              ? "Prediction error can only be attributed once predictions have been compared against what actually happened. Record a purchase and its sale, and the comparison starts here."
              : `A segment needs at least ${report.minimum_sample} closed positions, a large enough average error, and most of those errors pointing the same way. Nothing clears that yet, which is not the same as the predictions being good.`
          }
        />
      ) : (
        <div className="space-y-3">
          {report.findings.map((finding) => (
            <Card
              key={`${finding.dimension}-${finding.segment}`}
              title={`${finding.segment}`}
              subtitle={`by ${finding.dimension.replace(/_/g, " ")}`}
            >
              <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
                <div>
                  <p className="text-[0.875rem] leading-relaxed text-secondary">
                    {finding.summary}
                  </p>
                  <p className="mt-3 text-xs leading-relaxed text-muted">
                    {finding.suggestion}
                  </p>
                </div>
                <div className="space-y-2">
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Average error</span>
                    <span
                      className={`numeric text-[0.8125rem] ${
                        finding.direction === "overestimated" ? "text-pass" : "text-buy"
                      }`}
                    >
                      {percent(finding.mean_signed_error)}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Pointing the same way</span>
                    <span className="numeric text-[0.8125rem] text-secondary">
                      {percent(finding.agreement, 0)}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between border-b border-hairline py-1.5">
                    <span className="text-2xs text-faint">Closed positions</span>
                    <span className="numeric text-[0.8125rem] text-secondary">
                      {finding.sample}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between py-1.5">
                    <span className="text-2xs text-faint">Money involved</span>
                    <span className="numeric text-[0.8125rem] text-primary">
                      {money(finding.impact)}
                    </span>
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {report.observations > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          <Stat label="Closed positions analysed" value={report.observations} />
          <Stat label="Patterns found" value={report.findings.length} />
          <Stat
            label="Minimum to call a pattern"
            value={report.minimum_sample}
            hint="closed positions in one segment"
          />
        </div>
      )}

      {report.insufficient.length > 0 && (
        <Card
          title="Looked at, not enough behind it"
          subtitle="Reported so the absence of a finding is visible rather than silent"
        >
          <div className="space-y-1.5">
            {report.insufficient.slice(0, 12).map((note) => (
              <div
                key={`${note.dimension}-${note.segment}`}
                className="flex flex-wrap items-baseline gap-x-3 text-2xs"
              >
                <span className="text-secondary">{note.segment}</span>
                <span className="text-faint">{note.reason}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Note>
        Findings, never actions. Nothing here retunes a weight, a fee assumption or a
        threshold: an engine that silently adjusts itself on a dozen observations is how
        a small sampling accident becomes a permanent rule. Acting on one of these is a
        decision you make on the policy page.
      </Note>
    </div>
  );
}
