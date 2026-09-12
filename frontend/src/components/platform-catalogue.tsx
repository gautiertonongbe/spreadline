import { Badge, Card, Note, Table, Td, Th, Tr } from "@/components/ui";
import { API_URL } from "@/lib/api";

/**
 * The platform catalogue, rendered from the running system rather than from a
 * roadmap document. Each row says what a platform would give us, what credential
 * it needs, what it costs and whether it needs a seller account, so the decision
 * to connect one can be made without reading the code.
 */

interface Platform {
  slug: string;
  display_name: string;
  role: string;
  status: "live" | "adapter_ready" | "planned";
  capabilities: string[];
  credentials: string[];
  signup_url: string;
  docs_url: string | null;
  cost: string;
  requires_seller_account: boolean;
  notes: string;
  caveats: string[];
}

interface PlatformResponse {
  platforms: Platform[];
  counts: Record<string, number>;
  connectable_without_a_seller_account: string[];
}

const STATUS_LABEL: Record<Platform["status"], string> = {
  live: "Live",
  adapter_ready: "Adapter ready",
  planned: "Planned",
};

const STATUS_TONE: Record<Platform["status"], "success" | "accent" | "neutral"> = {
  live: "success",
  adapter_ready: "accent",
  planned: "neutral",
};

const STATUS_ORDER: Platform["status"][] = ["live", "adapter_ready", "planned"];

const STATUS_BLURB: Record<Platform["status"], string> = {
  live: "Implemented and wired to a real endpoint. Set the credential and it starts returning market data.",
  adapter_ready:
    "Interface, capabilities and payload mapper are implemented and tested. Connecting one is finishing a transport method, not designing an integration.",
  planned:
    "Described but not implemented. It registers, reports itself unavailable with the reason, and refuses calls rather than returning a plausible empty result.",
};

export async function PlatformCatalogue() {
  let data: PlatformResponse | null = null;
  try {
    const response = await fetch(`${API_URL}/platforms`, { cache: "no-store" });
    if (response.ok) data = (await response.json()) as PlatformResponse;
  } catch {
    // The catalogue is informational; it must not break the page.
  }

  if (!data) return null;

  const free = data.platforms.filter(
    (item) => item.status === "live" && !item.requires_seller_account,
  );

  return (
    <div className="space-y-4">
      <Card
        title="Platform catalogue"
        subtitle={`${data.counts.live ?? 0} live, ${data.counts.adapter_ready ?? 0} adapter ready, ${data.counts.planned ?? 0} planned. Connecting one is a credential, not a rebuild.`}
        flush
      >
        {free.length > 0 && (
          <div className="border-b border-hairline px-5 py-4">
            <Note>
              <span className="text-secondary">
                Real data with no seller account and no subscription:
              </span>{" "}
              {free.map((item) => item.display_name).join(" and ")}. A free developer
              key for each is enough. Everything else in this table needs either a
              seller account or a paid plan.
            </Note>
          </div>
        )}

        <Table>
          <thead>
            <tr>
              <Th>Platform</Th>
              <Th>Role</Th>
              <Th>Status</Th>
              <Th>Cost</Th>
              <Th>Credentials</Th>
              <Th>Capabilities</Th>
            </tr>
          </thead>
          <tbody>
            {STATUS_ORDER.flatMap((status) =>
              data!.platforms
                .filter((item) => item.status === status)
                .map((item) => (
                  <Tr key={item.slug}>
                    <Td>
                      <a
                        href={item.docs_url ?? item.signup_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-secondary transition hover:text-accent"
                      >
                        {item.display_name}
                      </a>
                      <div className="mt-0.5 max-w-md text-2xs leading-relaxed text-faint">
                        {item.notes}
                      </div>
                      {item.caveats.map((caveat) => (
                        <div key={caveat} className="mt-1 text-2xs text-review">
                          {caveat}
                        </div>
                      ))}
                    </Td>
                    <Td className="capitalize text-muted">{item.role}</Td>
                    <Td>
                      <Badge tone={STATUS_TONE[item.status]}>
                        {STATUS_LABEL[item.status]}
                      </Badge>
                    </Td>
                    <Td className="text-2xs text-muted">
                      {item.cost}
                      {item.requires_seller_account && (
                        <div className="mt-0.5 text-review">Seller account required</div>
                      )}
                    </Td>
                    <Td>
                      <div className="space-y-0.5">
                        {item.credentials.map((credential) => (
                          <div key={credential} className="font-mono text-2xs text-faint">
                            {credential}
                          </div>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <div className="flex max-w-[220px] flex-wrap gap-1">
                        {item.capabilities.map((capability) => (
                          <span
                            key={capability}
                            className="rounded bg-raised px-1.5 py-0.5 text-3xs text-faint"
                          >
                            {capability}
                          </span>
                        ))}
                      </div>
                    </Td>
                  </Tr>
                )),
            )}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-3 lg:grid-cols-3">
        {STATUS_ORDER.map((status) => (
          <div
            key={status}
            className="rounded-lg border border-border bg-surface px-4 py-3.5"
          >
            <div className="flex items-center gap-2">
              <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
              <span className="numeric text-xs text-muted">
                {data.counts[status] ?? 0}
              </span>
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-faint">
              {STATUS_BLURB[status]}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
