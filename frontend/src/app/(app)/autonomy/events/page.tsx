import Link from "next/link";

import { Card, EmptyState, ErrorState, PageHeader } from "@/components/ui";
import { autonomyApi, type AutonomyEventView } from "@/lib/api";
import { date, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

const TONE: Record<string, string> = {
  decision_authorized: "bg-buy",
  decision_blocked: "bg-pass",
  decision_escalated: "bg-review",
  breaker_tripped: "bg-pass",
  breaker_reset: "bg-buy",
  emergency_stop: "bg-pass",
  emergency_stop_cleared: "bg-buy",
  policy_changed: "bg-accent",
  level_changed: "bg-accent",
  experiment_started: "bg-accent",
  experiment_stopped: "bg-muted",
  human_override: "bg-review",
};

/**
 * The audit log.
 *
 * Every autonomous action and every instruction given to it, in one append-only
 * sequence. Read newest first, because the question is almost always "what just
 * happened".
 */
export default async function AutonomyEventsPage() {
  let data: { items: AutonomyEventView[] };
  try {
    data = await autonomyApi.events();
  } catch (error) {
    return <ErrorState message={`Could not load the audit log. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Audit log"
        description="Every autonomous action, and every instruction a person gave it."
        actions={
          <Link href="/autonomy" className="text-xs text-accent hover:underline">
            Back to autonomy
          </Link>
        }
      />

      {data.items.length === 0 ? (
        <EmptyState
          title="Nothing recorded yet"
          description="Policy changes, decisions, circuit breakers and emergency stops all appear here as they happen."
        />
      ) : (
        <Card>
          <ol className="space-y-0">
            {data.items.map((event) => (
              <li
                key={event.id}
                className="flex items-start gap-3 border-b border-hairline py-3 last:border-b-0"
              >
                <span
                  className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${
                    TONE[event.type] ?? "bg-faint"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <span className="text-[0.8125rem] text-primary">
                      {titleCase(event.type)}
                    </span>
                    <span className="text-2xs text-faint">{date(event.created_at)}</span>
                    <span className="text-2xs text-faint">by {event.actor}</span>
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">
                    {event.message}
                  </p>
                  {event.decision_id && (
                    <Link
                      href={`/autonomy/decisions/${event.decision_id}`}
                      className="mt-1 inline-block text-2xs text-accent hover:underline"
                    >
                      The decision
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </div>
  );
}
