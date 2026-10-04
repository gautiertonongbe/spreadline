"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Button,
  Card,
  ErrorState,
  PageHeader,
  RecommendationBadge,
  RiskBadge,
  Stat,
  Table,
  Td,
  Th,
  Tr,
  Value,
} from "@/components/ui";
import { endpoints } from "@/lib/api";
import { money, percent, score } from "@/lib/format";

interface BulkRow {
  line_number: number;
  input: string;
  status: string;
  message: string | null;
  opportunity_id?: string;
  title?: string;
  recommendation?: string;
  score?: string;
  net_profit?: string;
  roi?: string | null;
  risk_level?: string;
  match_confidence?: string;
  headline?: string;
}

interface BulkResult {
  total: number;
  analyzed: number;
  errors: number;
  skipped: number;
  notes: string[];
  results: BulkRow[];
  failures: BulkRow[];
}

const EXAMPLE = `identifier
WM-598712344
WM-118820043
https://www.amazon.com/dp/B09XS7JWHH
027242923058`;

export default function BulkPage() {
  const [content, setContent] = useState(EXAMPLE);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult((await endpoints.bulkAnalyze(content)) as unknown as BulkResult);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Bulk analysis"
        description="Paste identifiers or marketplace URLs, one per line, or a CSV with columns such as identifier, asin, walmart_id, upc or url. Rows that fail are reported with their reason rather than dropped."
      />

      <Card>
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={8}
          spellCheck={false}
          className="w-full rounded border border-border bg-canvas px-3 py-2.5 font-mono text-xs leading-relaxed text-primary outline-none transition focus:border-accent/50"
        />
        <div className="mt-3 flex items-center gap-3">
          <Button tone="accent" onClick={() => void run()} disabled={busy}>
            {busy ? "Analysing" : "Analyse all"}
          </Button>
          <span className="text-2xs text-faint">
            Runs the full pipeline per row, so a large file takes a while.
          </span>
        </div>
      </Card>

      {error && <ErrorState message={error} />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Rows" value={result.total} />
            <Stat label="Analysed" value={result.analyzed} tone="buy" />
            <Stat label="Skipped" value={result.skipped} tone="muted" />
            <Stat label="Errors" value={result.errors} tone={result.errors ? "pass" : "muted"} />
          </div>

          {result.results.length > 0 && (
            <Card title="Ranked results" flush>
              <Table>
                <thead>
                  <tr>
                    <Th align="right">Score</Th>
                    <Th>Product</Th>
                    <Th>Call</Th>
                    <Th align="right">Profit</Th>
                    <Th align="right">ROI</Th>
                    <Th>Risk</Th>
                    <Th align="right">Match</Th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((row) => (
                    <Tr key={row.line_number}>
                      <Td align="right" numeric className="font-semibold">
                        {score(row.score ?? null)}
                      </Td>
                      <Td>
                        {row.opportunity_id ? (
                          <Link
                            href={`/opportunities/${row.opportunity_id}`}
                            className="text-accent hover:underline"
                          >
                            <span className="block max-w-sm truncate">{row.title}</span>
                          </Link>
                        ) : (
                          <span className="block max-w-sm truncate">{row.title}</span>
                        )}
                        <span className="font-mono text-2xs text-muted">{row.input}</span>
                      </Td>
                      <Td>
                        {row.recommendation && (
                          <RecommendationBadge value={row.recommendation} />
                        )}
                      </Td>
                      <Td
                        align="right"
                        numeric
                        className={Number(row.net_profit ?? 0) >= 0 ? "text-buy" : "text-pass"}
                      >
                        {money(row.net_profit ?? null)}
                      </Td>
                      <Td align="right" numeric>
                        <Value>{percent(row.roi ?? null)}</Value>
                      </Td>
                      <Td>{row.risk_level && <RiskBadge value={row.risk_level} />}</Td>
                      <Td align="right" numeric>
                        <Value>{percent(row.match_confidence ?? null, 0)}</Value>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}

          {result.failures.length > 0 && (
            <Card title="Rows that could not be analysed" flush>
              <Table>
                <thead>
                  <tr>
                    <Th align="right">Line</Th>
                    <Th>Input</Th>
                    <Th>Status</Th>
                    <Th>Reason</Th>
                  </tr>
                </thead>
                <tbody>
                  {result.failures.map((row) => (
                    <Tr key={`${row.line_number}-${row.input}`}>
                      <Td align="right" numeric className="text-muted">
                        {row.line_number}
                      </Td>
                      <Td className="font-mono text-2xs">{row.input}</Td>
                      <Td className="capitalize text-muted">{row.status}</Td>
                      <Td className="text-muted">{row.message}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
