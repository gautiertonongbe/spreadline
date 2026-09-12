import { API_URL, type ProviderHealth } from "@/lib/api";

/**
 * Says, permanently and without being asked, whether the numbers on screen came
 * from a market or from a fixture.
 *
 * This is not decoration. Every figure in the app is a price, a profit or a
 * decision, and the difference between "Amazon said this" and "a fixture said
 * this" is the difference between a tool and a demo. Burying it in a tooltip
 * would be the single most misleading thing the interface could do.
 */
export async function DataSourceBanner({
  variant = "sidebar",
}: {
  variant?: "sidebar" | "compact";
}) {
  let providers: ProviderHealth[] = [];
  try {
    const response = await fetch(`${API_URL}/providers/health`, { cache: "no-store" });
    if (response.ok) providers = (await response.json()) as ProviderHealth[];
  } catch {
    // The banner must never be the reason a page fails to render.
  }

  if (providers.length === 0) {
    return variant === "compact" ? null : (
      <div className="text-3xs uppercase tracking-label text-faint">
        Provider status unavailable
      </div>
    );
  }

  const usable = providers.filter((item) => item.is_configured);
  const live = usable.filter((item) => item.is_live);
  const allFixture = usable.length > 0 && live.length === 0;

  if (variant === "compact") {
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded px-2 py-[3px] text-3xs font-medium uppercase tracking-label ring-1 ring-inset ${
          allFixture
            ? "bg-review/10 text-review ring-review/25"
            : "bg-buy/10 text-buy ring-buy/25"
        }`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${allFixture ? "bg-review" : "bg-buy"}`}
        />
        {allFixture ? "Fixture data" : "Live data"}
      </span>
    );
  }

  return (
    <div>
      <div className="label mb-2">Data source</div>
      <div className="flex items-center gap-2">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            allFixture ? "bg-review" : "bg-buy"
          }`}
        />
        <span
          className={`text-xs font-medium ${allFixture ? "text-review" : "text-buy"}`}
        >
          {allFixture ? "Fixture data" : "Live market data"}
        </span>
      </div>
      <p className="mt-2 text-3xs leading-relaxed tracking-normal text-faint">
        {allFixture
          ? "Every price and decision on screen comes from the fixture catalogue, not from a marketplace. Add provider credentials to switch."
          : `${live.length} live provider${live.length === 1 ? "" : "s"} configured.`}
      </p>
      <a
        href="/providers"
        className="mt-2 inline-block text-3xs uppercase tracking-label text-faint transition hover:text-accent"
      >
        Provider status
      </a>
    </div>
  );
}
