import { Badge, Card, ErrorState, Table, Td, Th, Value } from "@/components/ui";
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

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Providers</h1>
        <p className="mt-1 text-sm text-muted">
          Every data source, what it can answer, and how reliably it has been
          answering. A provider without credentials is listed with the reason
          rather than hidden.
        </p>
      </div>

      <Card>
        <Table>
          <thead>
            <tr>
              <Th>Provider</Th>
              <Th>State</Th>
              <Th>Data</Th>
              <Th align="right">Requests</Th>
              <Th align="right">Success</Th>
              <Th align="right">Latency</Th>
              <Th align="right">p95</Th>
              <Th>Circuit</Th>
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <tr key={provider.slug}>
                <Td>
                  <div>{provider.display_name}</div>
                  <div className="font-mono text-2xs text-muted">{provider.slug}</div>
                  {provider.configuration_note && (
                    <div className="mt-1 max-w-md text-2xs text-muted">
                      {provider.configuration_note}
                    </div>
                  )}
                </Td>
                <Td>
                  <Badge tone={STATE_TONE[provider.state] ?? "neutral"}>
                    {titleCase(provider.state)}
                  </Badge>
                </Td>
                <Td>
                  <Badge tone={provider.is_live ? "accent" : "warning"}>
                    {provider.is_live ? "Live market" : "Fixture"}
                  </Badge>
                </Td>
                <Td align="right" numeric>
                  {provider.request_count}
                </Td>
                <Td align="right" numeric>
                  <Value>
                    {provider.success_rate === null
                      ? "not available"
                      : percent(provider.success_rate, 0)}
                  </Value>
                </Td>
                <Td align="right" numeric className="text-muted">
                  {provider.avg_latency_ms === null ? "not available" : `${provider.avg_latency_ms} ms`}
                </Td>
                <Td align="right" numeric className="text-muted">
                  {provider.p95_latency_ms === null ? "not available" : `${provider.p95_latency_ms} ms`}
                </Td>
                <Td className="capitalize text-muted">{provider.circuit_state}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <Card title="Capabilities">
        <div className="grid gap-4 lg:grid-cols-2">
          {providers.map((provider) => (
            <div key={provider.slug}>
              <div className="text-sm">{provider.display_name}</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {provider.capabilities.map((capability) => (
                  <Badge key={capability}>{capability}</Badge>
                ))}
              </div>
              {provider.last_error && (
                <div className="mt-2 text-2xs text-pass">Last error: {provider.last_error}</div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
