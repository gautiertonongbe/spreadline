/**
 * API client.
 *
 * Two rules shape this file:
 *
 * 1. Money never becomes a JavaScript number. The API sends decimal strings and
 *    they stay strings until they are formatted for display. Parsing a profit
 *    into a float to render it is how 71.6496 becomes 71.64959999999999.
 * 2. Nullable fields stay nullable all the way to the component. A missing ROI
 *    is rendered as "not available", never as 0%.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export type Recommendation = "buy" | "review" | "pass";
export type RiskLevel = "low" | "medium" | "high" | "critical";
export type Confidence = "high" | "medium" | "low" | "none";

/** A decimal value carried as a string, exactly as the API sent it. */
export type Money = string;

export interface ApiError {
  error: { code: string; message: string; fields?: unknown[] };
}

export class RequestFailed extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "RequestFailed";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    // Analytical data is always fetched fresh: a cached opportunity table would
    // show prices that no longer exist.
    cache: "no-store",
  });

  if (!response.ok) {
    let code = "request_failed";
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as ApiError;
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // A non-JSON error body (a proxy timeout, say) keeps the status text.
    }
    throw new RequestFailed(response.status, code, message);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
};

// ---------------------------------------------------------------- types

export interface OpportunitySummary {
  id: string;
  product_id: string;
  /** Denormalised by the list endpoint so a row is identifiable as a product. */
  title: string | null;
  brand: string | null;
  direction: string;
  sourcing_channel: string;
  source_marketplace: string;
  target_marketplace: string;
  status: string;
  recommendation: Recommendation;
  score: Money | null;
  acquisition_cost: Money | null;
  expected_sale_price: Money | null;
  net_profit: Money | null;
  roi: Money | null;
  margin: Money | null;
  spread: Money | null;
  risk_level: RiskLevel;
  risk_score: Money | null;
  match_confidence: Money | null;
  data_quality_score: Money | null;
  demand_confidence: Confidence;
  analyzed_at: string | null;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ScoreComponent {
  name: string;
  score: Money;
  weight: Money;
  contribution: Money;
  basis: string;
  has_data: boolean;
}

export interface Gate {
  code: string;
  label: string;
  passed: boolean;
  detail: string;
  hard: boolean;
}

export interface RiskSignal {
  code: string;
  severity: string;
  weight: Money;
  points: Money;
  message: string;
  evidence: string[];
  blocking: boolean;
}

export interface LineItem {
  code: string;
  label: string;
  amount: Money;
  sign: number;
  basis: string;
}

export interface Economics {
  sale_price: Money;
  acquisition_cost: Money;
  acquisition_shipping: Money;
  referral_fee: Money;
  fulfillment_fee: Money;
  closing_fee: Money;
  storage_fee: Money;
  inbound_shipping: Money;
  return_allowance: Money;
  tax: Money;
  misc_cost: Money;
  total_fees: Money;
  total_cost: Money;
  net_profit: Money;
  spread?: Money;
  roi: Money | null;
  margin: Money | null;
  breakeven_sale_price: Money | null;
  max_acquisition_cost: Money | null;
  line_items: LineItem[];
  assumptions_version: string;
  assumptions?: Record<string, unknown>;
  warnings?: string[];
  fulfillment?: string;
}

export interface WindowStats {
  window_days: number;
  observation_count: number;
  distinct_days: number;
  coverage: Money;
  sufficient: boolean;
  confidence: Confidence;
  average: Money | null;
  median: Money | null;
  minimum: Money | null;
  maximum: Money | null;
  stdev: Money | null;
  volatility: Money | null;
  trend: string;
  trend_pct: Money | null;
  max_drawdown: Money | null;
  reason: string | null;
}

export interface PriceHistory {
  current_price: Money | null;
  observation_count: number;
  history_span_days: number;
  reference_window: number | null;
  confidence: Confidence;
  windows: Record<string, WindowStats>;
}

export interface AnalysisResponse {
  opportunity_id: string | null;
  summary: {
    product_id: string | null;
    title: string;
    brand: string | null;
    category: string | null;
    direction: string;
    sourcing_channel: string;
    analyzed_at: string;
    is_live_data: boolean;
    source: MarketSide;
    target: MarketSide;
    warnings: string[];
  };
  match: {
    confidence: number;
    method: string;
    status: string;
    evidence: { type: string; detail: string }[];
    conflicts: { dimension: string; message: string; blocking: boolean }[];
    variation_passed: boolean;
    title_similarity: number;
    summary: string;
    rejection_reason: string | null;
  };
  economics: Economics;
  source_prices: PriceHistory;
  target_prices: PriceHistory;
  source_anomaly: Anomaly;
  target_anomaly: Anomaly;
  demand: DemandAssessment;
  competition: CompetitionAssessment;
  data_quality: DataQuality;
  risk: { level: RiskLevel; score: Money; summary: string; signals: RiskSignal[] };
  score: {
    total: Money;
    raw_total: Money | null;
    gated_reason: string | null;
    model_version: string;
    components: ScoreComponent[];
    unknown_components: string[];
  };
  decision: {
    recommendation: Recommendation;
    headline: string;
    reasons: string[];
    risks: string[];
    gates: Gate[];
    policy_version: string;
  };
  stress_test: StressTest | null;
  notes: string[];
}

export interface MarketSide {
  marketplace: string;
  external_id: string;
  price: Money | null;
  availability: string;
  quantity_available?: number | null;
  seller_count?: number | null;
  url: string | null;
}

export interface Anomaly {
  type: string;
  severity: string;
  confidence: Confidence;
  message: string;
  deviation: Money | null;
  reference_median: Money | null;
  reference_window: number | null;
  evidence: string[];
}

export interface DemandAssessment {
  score: Money | null;
  confidence: Confidence;
  current_rank: number | null;
  median_rank: number | null;
  rank_category: string | null;
  rank_trend: string;
  review_velocity_30d: Money | null;
  current_review_count: number | null;
  estimated_monthly_units: number | null;
  estimation_basis: string | null;
  observation_count: number;
  span_days: number;
  reasons: string[];
}

export interface CompetitionAssessment {
  seller_count: number | null;
  offer_count: number | null;
  average_seller_count_30d: Money | null;
  seller_trend: string;
  seller_change_pct: Money | null;
  entrants_30d: number | null;
  exits_30d: number | null;
  buy_box_price: Money | null;
  lowest_offer: Money | null;
  median_offer: Money | null;
  highest_offer: Money | null;
  price_dispersion: Money | null;
  marketplace_is_seller: boolean;
  risk_level: RiskLevel;
  pressure_score: Money;
  confidence: Confidence;
  reasons: string[];
}

export interface DataQuality {
  score: Money;
  confidence: Confidence;
  dimensions: {
    dimension: string;
    confidence: Confidence;
    score: Money;
    reason: string;
    is_stale: boolean;
  }[];
  missing: string[];
  stale: string[];
}

export interface StressScenario {
  key: string;
  label: string;
  description: string;
  sale_price: Money;
  acquisition_cost: Money;
  total_fees: Money;
  net_profit: Money;
  roi: Money | null;
  margin: Money | null;
  risk_level: RiskLevel;
  profit_delta: Money | null;
  survives: boolean;
  notes: string[];
}

export interface StressTest {
  scenarios: StressScenario[];
  surviving_count: number;
  total_count: number;
  survival_rate: Money;
  worst_case: StressScenario | null;
  price_headroom: Money | null;
}

export interface DashboardResponse {
  opportunities: {
    total: number;
    by_recommendation: Record<string, number>;
    by_risk_level: Record<string, number>;
    by_status: Record<string, number>;
    average_score: Money | null;
    buy_expected_profit: Money;
    buy_capital_required: Money;
  };
  portfolio: {
    capital_deployed: Money;
    capital_open: Money;
    gross_revenue: Money;
    net_revenue: Money;
    actual_profit: Money;
    actual_roi: Money | null;
    purchase_count: number;
    sale_count: number;
    open_positions: number;
    closed_positions: number;
    win_rate: Money | null;
    average_days_to_sell: Money | null;
  };
  prediction_accuracy: {
    sample_size: number;
    is_meaningful: boolean;
    predicted_profit_total?: Money;
    actual_profit_total?: Money;
    total_variance?: Money;
    mean_absolute_pct_error?: Money | null;
    caveat: string | null;
  };
  validation: {
    validated_count: number;
    target_count: number;
    progress_pct: Money;
    is_statistically_meaningful: boolean;
    match_accuracy: { rate: Money | null; correct: number; checked: number };
    buy_precision: { rate: Money | null; confirmed: number; checked: number };
    false_positive_rate: { rate: Money | null; false_positives: number; checked: number };
    caveat: string | null;
  };
}

export interface ProviderInfo {
  slug: string;
  marketplace: string;
  display_name: string;
  capabilities: string[];
  is_configured: boolean;
  is_live: boolean;
  configuration_note: string | null;
}

export interface ProviderHealth {
  slug: string;
  marketplace: string;
  display_name: string;
  capabilities: string[];
  is_configured: boolean;
  is_live: boolean;
  /** "live" calls a market, "fixture" serves canned data, "planned" is unimplemented. */
  kind: "live" | "fixture" | "planned";
  configuration_note: string | null;
  state: string;
  circuit_state: string;
  request_count: number;
  success_count: number;
  failure_count: number;
  success_rate: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  last_error: string | null;
}

export interface SearchItem {
  marketplace: string;
  external_id: string;
  title: string;
  brand: string | null;
  category: string | null;
  price: Money | null;
  availability: string;
  seller_count: number | null;
  sales_rank: number | null;
  url: string | null;
  provider: string;
  is_live_data: boolean;
}

// ------------------------------------------------------------- endpoints

export const endpoints = {
  dashboard: () => api.get<DashboardResponse>("/analytics"),
  opportunities: (query = "") =>
    api.get<Page<OpportunitySummary>>(`/opportunities${query}`),
  opportunity: (id: string) => api.get<Record<string, unknown>>(`/opportunities/${id}`),
  search: (body: { query: string; marketplace: string; limit?: number }) =>
    api.post<{ items: SearchItem[]; total_results: number | null }>(
      "/products/search",
      body,
    ),
  analyze: (body: {
    source_marketplace: string;
    source_external_id: string;
    target_marketplace: string;
    target_external_id?: string | null;
  }) => api.post<AnalysisResponse>("/products/analyze", body),
  providers: () => api.get<ProviderHealth[]>("/providers/health"),
  simulateCapital: (body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>("/simulate/capital", body),
  bulkAnalyze: (content: string) =>
    api.post<Record<string, unknown>>("/bulk/analyze", { content }),
  decide: (id: string, status: string, note?: string) =>
    api.post<OpportunitySummary>(`/opportunities/${id}/decision`, { status, note }),
  validate: (id: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/opportunities/${id}/validate`, body),
};
