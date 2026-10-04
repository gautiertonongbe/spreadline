import Link from "next/link";

import { Card, EmptyState, ErrorState, Note, PageHeader, Stat } from "@/components/ui";
import { autonomyApi, type SellRecommendation, type SellReview } from "@/lib/api";
import { money } from "@/lib/format";

export const dynamic = "force-dynamic";

interface ActionCopy {
  label: string;
  tone: string;
  dot: string;
  meaning: string;
}

const HOLD: ActionCopy = {
  label: "Hold",
  tone: "text-muted",
  dot: "bg-buy",
  meaning: "Doing what it was bought to do.",
};

const ACTION: Record<string, ActionCopy> = {
  liquidate: {
    label: "Get the capital back",
    tone: "text-pass",
    dot: "bg-pass",
    meaning:
      "Past the age limit or under water. The question is no longer the last few percent of profit.",
  },
  sell_now: {
    label: "Sell now",
    tone: "text-review",
    dot: "bg-review",
    meaning:
      "Still profitable, but something is eroding it. Taking it now beats waiting for a price moving the wrong way.",
  },
  reprice: {
    label: "Come down on price",
    tone: "text-accent",
    dot: "bg-accent",
    meaning: "Not selling, but the economics survive a lower price.",
  },
  hold: HOLD,
};

function Row({ item }: { item: SellRecommendation }) {
  const action = ACTION[item.action] ?? HOLD;
  return (
    <div className="border-b border-hairline px-5 py-4 last:border-b-0">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:gap-6">
        <div className="flex shrink-0 items-center gap-2 lg:w-[190px]">
          <span className={`h-1.5 w-1.5 rounded-full ${action.dot}`} />
          <span className={`text-[0.8125rem] font-medium ${action.tone}`}>
            {action.label}
          </span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[0.8125rem] leading-relaxed text-secondary">{item.summary}</p>
          <ul className="mt-1.5 space-y-0.5">
            {item.reasons.map((reason) => (
              <li key={reason} className="text-2xs leading-relaxed text-faint">
                {reason}
              </li>
            ))}
          </ul>
        </div>
        <div className="grid shrink-0 grid-cols-3 gap-x-6 text-right lg:w-[290px]">
          <div>
            <div className="label">At risk</div>
            <div className="numeric mt-1 text-[0.8125rem] text-primary">
              {money(item.capital_at_risk)}
            </div>
          </div>
          <div>
            <div className="label">Floor</div>
            <div className="numeric mt-1 text-[0.8125rem] text-muted">
              {item.floor_price ? money(item.floor_price) : "not available"}
            </div>
          </div>
          <div>
            <div className="label">Held</div>
            <div className="numeric mt-1 text-[0.8125rem] text-muted">{item.days_held}d</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The exit side of the decision.
 *
 * Ordered by urgency then by money, because a page of positions is only useful
 * if the ones that need a decision today are the ones at the top.
 */
export default async function SellReviewPage() {
  let data: SellReview;
  try {
    data = await autonomyApi.sellReview();
  } catch (error) {
    return <ErrorState message={`Could not load the review. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="What to sell"
        description="Buying well is half the decision. This is the other half, read from what the exit market is doing now rather than from the spread that justified the purchase."
        actions={
          <Link href="/autonomy/positions" className="text-xs text-accent hover:underline">
            All positions
          </Link>
        }
      />

      {data.total === 0 ? (
        <EmptyState
          title="No open positions"
          description="Once capital is committed, every open position is reviewed here against the current exit price, its age and what is left to make."
          action={
            <Link href="/autonomy" className="text-xs text-accent hover:underline">
              Open autonomy
            </Link>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              label="Need a decision"
              value={data.needs_attention}
              tone={data.needs_attention > 0 ? "review" : "default"}
              hint={`of ${data.total} open`}
            />
            <Stat
              label="Capital involved"
              value={money(data.capital_needing_attention)}
              hint="in the positions that need one"
            />
            <Stat label="Hold" value={data.by_action.hold ?? 0} />
            <Stat
              label="Act"
              value={
                (data.by_action.sell_now ?? 0) +
                (data.by_action.reprice ?? 0) +
                (data.by_action.liquidate ?? 0)
              }
              hint="sell, reprice or liquidate"
            />
          </div>

          <Card flush>
            {data.items.map((item) => (
              <Row key={item.position_id} item={item} />
            ))}
          </Card>
        </>
      )}

      <Note>{data.note}</Note>
    </div>
  );
}
