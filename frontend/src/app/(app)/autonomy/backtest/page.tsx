import Link from "next/link";

import { BacktestRunner } from "@/components/backtest-runner";
import { ErrorState, Note, PageHeader } from "@/components/ui";
import { autonomyApi, type AutonomyOverview } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * What the policy would have done.
 *
 * This is the last piece of evidence available before real capital moves, and
 * the easiest one in the product to over-read. The page is built so the
 * assumptions arrive with the number rather than under it: the replay's caveats
 * render above the return, and the candidates it could not judge are named
 * rather than counted.
 */
export default async function BacktestPage() {
  let overview: AutonomyOverview;
  try {
    overview = await autonomyApi.overview();
  } catch (error) {
    return <ErrorState message={`Could not load the policy. ${(error as Error).message}`} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="What this policy would have done"
        description="Replay the current limits against the price history Spreadline recorded itself. At every simulated day the replay sees only what had been observed by then, and prices the trade with the same engine the live path uses."
        actions={
          <div className="flex items-center gap-3">
            <Link href="/autonomy/policy" className="text-xs text-accent hover:underline">
              Policy
            </Link>
            <Link href="/autonomy" className="text-xs text-accent hover:underline">
              Back to autonomy
            </Link>
          </div>
        }
      />

      <Note>
        A replay is evidence, not a forecast. It says how these limits would have handled
        the way prices actually moved, on the products already being observed. It cannot
        say whether a unit would have sold, and it is only as good as the history behind
        it: a window with little observed history produces a confident-looking number
        about almost nothing.
      </Note>

      <BacktestRunner defaultCapital={overview.policy.capital_limit} />
    </div>
  );
}
