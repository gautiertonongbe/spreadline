import {
  Badge,
  Card,
  ErrorState,
  Note,
  PageHeader,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { endpoints, type ProviderHealth } from "@/lib/api";
import { percent, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

const STATE_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  healthy: "success",
  degraded: "warning",
  unavailable: "danger",
  not_configured: "neutral",
};

export default async function ProvidersPage() {
  let providers: ProviderHealth[];
  try {
    providers = await endpoints.providers();
  } catch (error) {
    return <ErrorState message={`Could not load providers. ${(error as Error).message}`} />;
  }

  const live = providers.filter((item) => item.is_live && item.is_configured);
  const unconfigured = providers.filter((item) => !item.is_configured);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Providers"
        description="Every data source, what it can answer, and how reliably it has answered. A provider without credentials is listed with the reason rather than hidden."
      />

      {live.length === 0 && (
        <Note tone="warning">
          No live provider is configured, so every price, demand figure and
          decision in this application comes from the fixture catalogue rather
          than from a marketplace. The live adapters below are implemented and
          tested; they need credentials and a base URL to start returning market
          data.
        </Note>
      )}

      <Card flush>
        <Table>
          <thead>
            <tr>
              <Th>Provider</Th>
              <Th>Source</Th>
              <Th>State</Th>
              <Th align="right">Requests</Th>
              <Th align="right">Success</Th>
              <Th align="right">Latency</Th>
              <Th align="right">p95</Th>
              <Th>Circuit</Th>
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <Tr key={provider.slug}>
                <Td>
                  <div className="text-secondary">{provider.display_name}</div>
                  <div className="mt-0.5 font-mono text-2xs text-faint">
                    {provider.slug}
                  </div>
                </Td>
                <Td>
                  <Badge tone={provider.is_live ? "accent" : "warning"}>
                    {provider.is_live ? "Live market" : "Fixture"}
                  </Badge>
                </Td>
                <Td>
                  <Badge tone={STATE_TONE[provider.state] ?? "neutral"}>
                    {titleCase(provider.state)}
                  </Badge>
                </Td>
                <Td align="right" numeric className="text-secondary">
                  {provider.request_count}
                </Td>
                <Td align="right" numeric className="text-secondary">
                  <Value>
                    {provider.success_rate === null
                      ? "not available"
                      : percent(provider.success_rate, 0)}
                  </Value>
                </Td>
                <Td align="right" numeric className="text-muted">
                  {provider.avg_latency_ms === null
                    ? "not available"
                    : `${provider.avg_latency_ms} ms`}
                </Td>
                <Td align="right" numeric className="text-muted">
                  {provider.p95_latency_ms === null
                    ? "not available"
                    : `${provider.p95_latency_ms} ms`}
                </Td>
                <Td className="capitalize text-muted">{provider.circuit_state}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Capabilities" subtitle="What each provider is able to answer">
          <div className="space-y-4">
            {providers.map((provider) => (
              <div key={provider.slug}>
                <div className="text-xs text-secondary">{provider.display_name}</div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(provider.capabilities ?? []).length === 0 ? (
                    <span className="text-2xs text-faint">
                      No capabilities reported.
                    </span>
                  ) : (
                    (provider.capabilities ?? []).map((capability) => (
                      <Badge key={capability}>{capability}</Badge>
                    ))
                  )}
                </div>
                {provider.last_error && (
                  <div className="mt-2 text-2xs text-pass">
                    Last error: {provider.last_error}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card
          title="Configuration"
          subtitle="What each unconfigured provider is waiting for"
        >
          {unconfigured.length === 0 ? (
            <p className="text-xs text-muted">Every registered provider is configured.</p>
          ) : (
            <div className="space-y-4">
              {unconfigured.map((provider) => (
                <div key={provider.slug}>
                  <div className="text-xs text-secondary">{provider.display_name}</div>
                  <p className="mt-1 text-2xs leading-relaxed text-muted">
                    {provider.configuration_note ?? "No configuration note supplied."}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
