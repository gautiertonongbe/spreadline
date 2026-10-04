import { api, type ProviderHealth } from "@/lib/api";

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
    providers = await api.get<ProviderHealth[]>("/providers/health");
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
  const sandbox = usable.filter((item) => item.kind === "sandbox");

  // Three states, not two. A sandbox is neither: it calls real infrastructure
  // and gets invented prices back, so saying "fixture catalogue" would be wrong
  // about where the numbers came from and saying "live" would be wrong about
  // what they are.
  const source: "live" | "sandbox" | "fixture" = live.length
    ? "live"
    : sandbox.length
      ? "sandbox"
      : "fixture";
  const tone = {
    live: { dot: "bg-buy", text: "text-buy", chip: "bg-buy/10 text-buy ring-buy/25" },
    sandbox: {
      dot: "bg-review",
      text: "text-review",
      chip: "bg-review/10 text-review ring-review/25",
    },
    fixture: {
      dot: "bg-review",
      text: "text-review",
      chip: "bg-review/10 text-review ring-review/25",
    },
  }[source];
  const shortLabel = { live: "Live data", sandbox: "Sandbox data", fixture: "Fixture data" }[
    source
  ];
  const longLabel = {
    live: "Live market data",
    sandbox: "Sandbox data",
    fixture: "Fixture data",
  }[source];
  const detail = {
    live: `${live.length} live provider${live.length === 1 ? "" : "s"} configured.`,
    sandbox:
      "Real provider infrastructure, invented prices. The integration is proven; " +
      "no figure derived from it is a measurement, and every observation it writes " +
      "is recorded as simulated.",
    fixture:
      "Every price and decision on screen comes from the fixture catalogue, not from " +
      "a marketplace. Add provider credentials to switch.",
  }[source];

  if (variant === "compact") {
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded px-2 py-[3px] text-3xs font-medium uppercase tracking-label ring-1 ring-inset ${tone.chip}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
        {shortLabel}
      </span>
    );
  }

  return (
    <div>
      <div className="label mb-2">Data source</div>
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
        <span className={`text-xs font-medium ${tone.text}`}>{longLabel}</span>
      </div>
      <p className="mt-2 text-3xs leading-relaxed tracking-normal text-faint">{detail}</p>
      <a
        href="/providers"
        className="mt-2 inline-block text-3xs uppercase tracking-label text-faint transition hover:text-accent"
      >
        Provider status
      </a>
    </div>
  );
}
