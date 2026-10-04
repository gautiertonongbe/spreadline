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

const CONFIGURED_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

/** Loopback names for this machine. Aliases, not different sites. */
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/**
 * The API base, with loopback aliases reconciled to the page's own host.
 *
 * Cookie authentication treats `localhost` and `127.0.0.1` as different hosts,
 * so a page served from one that calls an API on the other logs in
 * successfully, is handed a cookie it will never send back, and bounces
 * straight to the sign-in screen with nothing to explain it. They are the same
 * machine, and the mismatch is almost always an environment variable somebody
 * else set.
 *
 * Only loopback is touched, and only the host: a real cross-site deployment is
 * left exactly as configured, because there the mismatch is a fact about the
 * deployment rather than a local accident, and papering over it would break a
 * setup that works.
 */
function resolveApiUrl(): string {
  if (typeof window === "undefined") return CONFIGURED_API_URL;
  try {
    const configured = new URL(CONFIGURED_API_URL);
    const page = window.location.hostname;
    if (LOOPBACK.has(configured.hostname) && LOOPBACK.has(page)) {
      configured.hostname = page;
      return configured.toString().replace(/\/$/, "");
    }
  } catch {
    // A base that is not a URL is a configuration error the caller will see on
    // the first request; nothing useful can be decided here.
  }
  return CONFIGURED_API_URL;
}

export const API_URL = resolveApiUrl();

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

/**
 * The caller's session, on the server.
 *
 * A server component's fetch carries no cookies of its own: the browser sent
 * them to Next, and Next has to hand them on. Read through a dynamic import so
 * that bundling this module into a client component does not drag `next/headers`
 * in with it, where it does not exist.
 *
 * This is why the web app and the API have to be same-site. A cookie set by the
 * API on another site is one the Next server can never see, and every page here
 * renders on the server.
 */
async function forwardedCookies(): Promise<Record<string, string>> {
  if (typeof window !== "undefined") return {};
  try {
    const { cookies } = await import("next/headers");
    const jar = await cookies();
    const header = jar.toString();
    return header ? { cookie: header } : {};
  } catch {
    // Outside a request scope (a build-time render, say) there is no jar, and a
    // request with no session is the honest result rather than a crash.
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await forwardedCookies()),
      ...(init?.headers ?? {}),
    },
    // In the browser the API is a different port, so the cookie only travels
    // when it is asked for explicitly.
    credentials: "include",
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

/** True for the error a page should answer by sending the reader to sign in. */
export function isNotAuthenticated(error: unknown): boolean {
  return error instanceof RequestFailed && error.status === 401;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
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
  /** The decision engine's own sentence, so a row can say why it was called. */
  headline: string | null;
  /** The first failed gate, hard gates first. Null when nothing is blocking. */
  primary_blocker: string | null;
  /** The most that can be paid per unit and still break even. */
  max_acquisition_cost: Money | null;
  gross_spread: Money | null;
  gross_spread_pct: Money | null;
  total_cost: Money | null;
  total_fees: Money | null;
  other_unit_costs: Money | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ScoreComponent {
  name: string;
  /** Plain-language name for display. */
  label: string;
  score: Money;
  weight: Money;
  contribution: Money;
  basis: string;
  /** The measured values that went in, already formatted. */
  inputs: Record<string, string>;
  /** The arithmetic actually performed, with the real numbers in it. */
  calculation: string;
  has_data: boolean;
}

export interface ScoreBreakdown {
  total: Money;
  raw_total: Money | null;
  gated_reason: string | null;
  model_version: string;
  components: ScoreComponent[];
  unknown_components: string[];
  /** The sum of the contributions, so the column adds up on the page. */
  contribution_total: Money;
  weight_total: Money;
}

export type RiskCategoryName =
  | "price"
  | "competition"
  | "demand"
  | "inventory"
  | "product_match"
  | "data_quality"
  | "brand_category"
  | "economics";

export interface RiskCategoryAssessment {
  category: RiskCategoryName;
  label: string;
  score: Money;
  level: RiskLevel;
  /** False when there was nothing to judge this category on. Not the same as clean. */
  has_evidence: boolean;
  summary: string;
  signals: RiskSignal[];
}

export interface RiskAssessment {
  level: RiskLevel;
  score: Money;
  summary: string;
  signals: RiskSignal[];
  categories: RiskCategoryAssessment[];
  driving_category: RiskCategoryName | null;
  unassessed_categories: RiskCategoryName[];
  model_version?: string;
}

export type SpreadVerdict = "structural" | "temporary" | "unstable" | "unsupported";

export interface SpreadSideEvidence {
  label: string;
  median: Money | null;
  volatility: Money | null;
  max_drawdown: Money | null;
  window_days: number | null;
  observation_count: number;
  confidence: Confidence;
  usable: boolean;
}

export interface SpreadEvidence {
  verdict: SpreadVerdict;
  confidence: Confidence;
  summary: string;
  current_spread: Money;
  typical_spread: Money | null;
  spread_ratio: Money | null;
  source: SpreadSideEvidence;
  target: SpreadSideEvidence;
  reasons: string[];
  version: string;
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
  category: RiskCategoryName;
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
  /** The same figure as `spread`, under the name the platform uses for it. */
  gross_spread?: Money;
  /** Gross spread against what was paid, not against the sale price. */
  gross_spread_pct?: Money | null;
  landed_acquisition_cost?: Money;
  /** Inbound shipping, tax and operator costs: the term that closes the ladder. */
  other_unit_costs?: Money;
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
    provenance: Provenance[];
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
  risk: RiskAssessment;
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
  spread_evidence: SpreadEvidence | null;
  notes: string[];
}

export interface Provenance {
  role: "source" | "target";
  marketplace: string;
  external_id: string;
  provider: string | null;
  /** null when the provider that wrote the row is no longer registered. */
  is_live_data: boolean | null;
  observed_at: string | null;
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
  /**
   * "live" calls a market, "sandbox" calls real infrastructure serving invented
   * prices, "fixture" serves canned data, "planned" is registered but
   * unimplemented. A sandbox is not a fixture: the integration is genuinely
   * proven and only the numbers are made up.
   */
  kind: "live" | "sandbox" | "fixture" | "planned";
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
  /**
   * Analyse a pair somebody read off two product pages and typed in.
   *
   * Calls no provider, so it needs no credential and nobody's approval. The
   * response is the ordinary analysis plus an `entry` block naming what the
   * omitted fields cost.
   */
  /**
   * Read a pasted product page into a draft.
   *
   * Writes nothing and analyses nothing: the draft fills in the form and the
   * person confirms it. The pasted text is not stored.
   */
  readPaste: (body: { text: string; marketplace?: string }) =>
    api.post<PasteDraft>("/products/analyze/paste", body),
  analyzeManual: (body: { source: ManualSideInput; target: ManualSideInput }) =>
    api.post<AnalysisResponse & { entry: ManualEntryReport }>(
      "/products/analyze/manual",
      body,
    ),
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

// ------------------------------------------------------- autonomy & history

export type ExecutionMode = "observe" | "shadow" | "live";
export type PositionStatus = "open" | "closed" | "cancelled";
export type DecisionOutcome = "authorized" | "blocked" | "escalated";
export type AgentVerdict = "proceed" | "review" | "reject" | "inconclusive";

export interface AutonomyPolicy {
  id: string;
  version: string;
  autonomy_level: number;
  autonomy_level_label: string;
  execution_mode: ExecutionMode;
  capital_limit: Money;
  max_position_size: Money | null;
  max_position_pct: Money;
  max_loss_per_position: Money | null;
  max_daily_deployment: Money | null;
  minimum_roi: Money;
  minimum_profit: Money;
  maximum_risk: RiskLevel;
  minimum_match_confidence: Money;
  minimum_data_quality: Money;
  maximum_inventory_age_days: number;
  maximum_category_exposure: Money;
  maximum_brand_exposure: Money;
  maximum_marketplace_exposure: Money;
  require_human_approval: boolean;
  emergency_stop_enabled: boolean;
  emergency_stop_active: boolean;
  emergency_stop_reason: string | null;
  /** Every switch that has to agree before capital moves. */
  may_deploy_capital: boolean;
}

export interface PolicyVersion {
  version: string;
  is_active: boolean;
  autonomy_level: number;
  capital_limit: Money;
  execution_mode: ExecutionMode;
  created_at: string | null;
  created_by: string | null;
  note: string | null;
}

export interface PerformanceMetric {
  key: string;
  label: string;
  /** Null when nothing has been measured. Never a zero standing in for one. */
  value: Money | null;
  sample: number;
  unit: "ratio" | "money" | "days" | "count";
  detail: string;
  is_meaningful: boolean;
}

export interface Scorecard {
  decisions_total: number;
  decisions_autonomous: number;
  human_overrides: number;
  meaningful_sample_threshold: number;
  metrics: PerformanceMetric[];
}

export interface GateCriterion {
  key: string;
  label: string;
  required: Money;
  direction: "min" | "max";
  unit: string;
  actual: Money | null;
  sample: number;
  met: boolean;
  note: string;
}

export interface GateEvaluation {
  target_level: number;
  target_level_label: string;
  target_capital_limit: Money;
  description: string;
  eligible: boolean;
  summary: string;
  criteria: GateCriterion[];
}

export interface BreakerReading {
  id: string;
  code: string;
  /** money, ratio or count. A threshold of 0.05 and one of 50 read differently. */
  unit: "money" | "ratio" | "count";
  label: string;
  description: string | null;
  is_enabled: boolean;
  threshold: Money | null;
  observed: Money;
  state: "ok" | "tripped";
  would_trip: boolean;
  tripped_reason: string | null;
  detail: string;
}

export interface CapitalPositionView {
  id: string;
  opportunity_id: string | null;
  decision_id: string | null;
  product_id: string | null;
  /** Null on a shadow position: how the two are told apart without a flag. */
  purchase_id: string | null;
  execution_mode: ExecutionMode;
  status: PositionStatus;
  title: string | null;
  brand: string | null;
  category: string | null;
  source_marketplace: string | null;
  target_marketplace: string | null;
  quantity: number;
  quantity_sold: number;
  unit_cost: Money;
  capital_invested: Money;
  expected_profit: Money | null;
  expected_roi: Money | null;
  realized_profit: Money;
  realized_proceeds: Money;
  unrealized_value: Money;
  risk_level: RiskLevel | null;
  days_held: number;
  opened_at: string | null;
  closed_at: string | null;
}

export interface PortfolioSummary {
  open_positions: number;
  closed_positions: number;
  capital_deployed: Money;
  expected_profit: Money;
  unrealized_value: Money;
  realized_profit: Money;
  realized_roi: Money | null;
  oldest_position_days: number;
  average_age_days: number;
  by_mode: Record<string, number>;
}

export interface AutonomyOverview {
  policy: AutonomyPolicy;
  level: {
    current: number;
    label: string;
    deploys_capital: boolean;
    execution_mode: ExecutionMode;
  };
  capital: { authorized: Money; deployed: Money; available: Money };
  portfolio: PortfolioSummary;
  performance: Scorecard;
  next_level: GateEvaluation | null;
  circuit_breakers: BreakerReading[];
  emergency_stop: { enabled: boolean; active: boolean; reason: string | null };
}

export interface DecisionSummary {
  id: string;
  opportunity_id: string | null;
  outcome: DecisionOutcome;
  reason_code: string | null;
  reason: string;
  policy_version: string;
  autonomy_level: number;
  execution_mode: ExecutionMode;
  quantity: number | null;
  capital_committed: Money | null;
  max_unit_price: Money | null;
  expected_profit: Money | null;
  stage_verdicts: Record<string, AgentVerdict>;
  created_at: string | null;
}

export interface AgentRunView {
  stage: string;
  verdict: AgentVerdict;
  summary: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface DecisionDetail extends DecisionSummary {
  evidence: Record<string, unknown>;
  runs: AgentRunView[];
}

export interface AutonomyEventView {
  id: string;
  type: string;
  actor: string;
  message: string;
  opportunity_id: string | null;
  decision_id: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface EligibilityCheck {
  code: string;
  label: string;
  passed: boolean;
  required: string;
  actual: string;
  detail: string;
}

export interface EligibilityResult {
  eligible: boolean;
  reason_code: string | null;
  summary: string;
  checks: EligibilityCheck[];
}

export interface DatasetTotals {
  tracked_listings: number;
  tracked_listings_active: number;
  price_observations: number;
  price_observations_real: number;
  price_observations_simulated: number;
  demand_observations: number;
  demand_observations_real: number;
  competition_observations: number;
  competition_observations_real: number;
  earliest_observation_at: string | null;
  windows_supported: number[];
}

export interface TrackedListing {
  id: string;
  listing_id: string | null;
  product_id: string | null;
  marketplace: string;
  external_id: string;
  reason: string;
  priority: number;
  is_active: boolean;
  refresh_interval_seconds: number;
  refresh_interval_is_default: boolean;
  last_refreshed_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  observation_count: number;
  /** Null means never polled, which means due now. */
  due_at: string | null;
  notes: string | null;
}

export interface ObservationCoverage {
  listing_id: string;
  price_observations: number;
  demand_observations: number;
  competition_observations: number;
  simulated_price_observations: number;
  real_price_observations: number;
  first_observed_at: string | null;
  last_observed_at: string | null;
  distinct_days: number;
  span_days: number;
}

export const autonomyApi = {
  overview: () => api.get<AutonomyOverview>("/autonomy"),
  policy: () =>
    api.get<{ policy: AutonomyPolicy; history: PolicyVersion[] }>("/autonomy/policy"),
  ladder: () =>
    api.get<{ current_level: number; levels: GateEvaluation[] }>("/autonomy/ladder"),
  positions: (query = "") =>
    api.get<{
      items: CapitalPositionView[];
      total: number;
      portfolio: PortfolioSummary;
    }>(`/autonomy/positions${query}`),
  decisions: (query = "") =>
    api.get<{ items: DecisionSummary[]; total: number }>(`/autonomy/decisions${query}`),
  decision: (id: string) => api.get<DecisionDetail>(`/autonomy/decisions/${id}`),
  events: () => api.get<{ items: AutonomyEventView[] }>("/autonomy/events?limit=60"),
  breakers: () =>
    api.get<{ items: BreakerReading[]; any_tripped: boolean }>("/autonomy/circuit-breakers"),

  // --- mutations. Every one of these is an explicit action by a person.
  enable: (body: {
    level: number;
    capital_limit?: string;
    execution_mode: ExecutionMode;
    acknowledge_not_eligible?: boolean;
    note?: string;
  }) => api.post<AutonomyPolicy>("/autonomy/enable", body),
  disable: () => api.post<AutonomyPolicy>("/autonomy/disable"),
  emergencyStop: (body: { active: boolean; reason: string }) =>
    api.post<AutonomyPolicy>("/autonomy/emergency-stop", body),
  resetBreaker: (id: string) =>
    api.post<{ id: string; code: string; state: string; reset_by: string }>(
      `/autonomy/circuit-breakers/${id}/reset`,
    ),
  runDecision: (body: { opportunity_id: string; available_capital?: string }) =>
    api.post<{
      decision: {
        outcome: DecisionOutcome;
        reason_code: string | null;
        reason: string;
        capital: Money;
        quantity: number;
        stages: { stage: string; verdict: AgentVerdict; summary: string }[];
      };
      decision_id: string | null;
      position: CapitalPositionView | null;
    }>("/autonomy/decisions/run", body),
  updatePolicy: (body: Record<string, unknown>) =>
    api.put<AutonomyPolicy>("/autonomy/policy", body),
  sellReview: (query = "") => api.get<SellReview>(`/autonomy/sell-review${query}`),
  learning: () => api.get<LearningReport>("/autonomy/learning"),
  backtest: (body: { days: number; starting_capital?: string; step_days?: number }) =>
    api.post<BacktestResult>("/autonomy/backtest", body),
  allocation: (query = "") =>
    api.get<AllocationPlan>(`/autonomy/allocation${query}`),
  velocity: () => api.get<VelocityReport>("/autonomy/velocity"),
  commitAllocation: (body: { budget?: string; expect_capital?: string }) =>
    api.post<AllocationCommit>("/autonomy/allocation/commit", body),
};

/**
 * A plan across every eligible candidate, rather than a decision about one.
 *
 * Every money field is a string, and `unallocated` is a deliberate figure: the
 * system is never required to deploy the capital it has.
 */
export interface AllocationLine {
  rank: number;
  opportunity_id: string;
  title: string;
  quantity: number;
  unit_cost: Money;
  capital: Money;
  expected_profit: Money;
  return_per_dollar: Money;
  max_unit_price: Money;
  limited_by: string;
  brand: string | null;
  category: string | null;
  marketplace: string | null;
  /** Measured, never assumed. Null until something has actually closed. */
  expected_days: number | null;
  annualized_return: Money | null;
  velocity_basis: string | null;
}

export interface VelocitySegment {
  dimension: string;
  segment: string;
  sample: number;
  median_days: number;
  fastest_days: number;
  slowest_days: number;
  median_roi: Money | null;
}

export interface VelocityReport {
  measured: boolean;
  summary: string;
  sample: number;
  minimum_sample: number;
  portfolio_median_days: number | null;
  median_annualized_roi: Money | null;
  segments: VelocitySegment[];
  insufficient: { dimension: string; segment: string; sample: number; reason: string }[];
  note: string;
}

export interface AllocationExcluded {
  opportunity_id: string;
  title: string;
  reason_code: string;
  detail: string;
  unit_cost: Money | null;
  return_per_dollar: Money | null;
  /** Present only where money was the binding constraint. */
  capital_needed: Money | null;
}

export interface AllocationConcentration {
  dimension: string;
  key: string;
  held: Money;
  planned: Money;
  total: Money;
  cap: Money;
  share_of_cap: Money | null;
}

export interface AllocationPlan {
  policy_version: string;
  execution_mode: ExecutionMode;
  summary: string;
  may_commit: boolean;
  blocked_reason: string | null;
  capital: {
    budget: Money;
    already_deployed: Money;
    allocated: Money;
    unallocated: Money;
    expected_profit: Money;
    expected_return: Money | null;
  };
  lines: AllocationLine[];
  excluded: AllocationExcluded[];
  concentration: AllocationConcentration[];
  considered: { eligible: number; ineligible: Record<string, number> };
  marginal_capital: AllocationExcluded | null;
  ranking: string;
  /** True when the ranking has time in it, because a position has closed. */
  ranked_on_time: boolean;
  velocity: VelocityReport;
  note: string;
}

export interface AllocationCommit {
  summary: string;
  plan_summary: string;
  authorized: number;
  refused: number;
  capital_committed: Money;
  outcomes: {
    opportunity_id: string;
    title: string;
    planned_quantity: number;
    planned_capital: Money;
    outcome: DecisionOutcome;
    reason_code: string | null;
    reason: string;
    quantity: number;
    capital: Money;
    decision_id: string | null;
    position_id: string | null;
  }[];
}

/**
 * A replay of the active policy over recorded history.
 *
 * Read the caveats before the return figure. The exit rule is an assumption,
 * not a measurement, and every field under `results` inherits it.
 */
export interface BacktestTrade {
  opportunity_id: string;
  title: string;
  opened_at: string;
  closed_at: string | null;
  entry_price: Money;
  expected_sale_price: Money;
  exit_price: Money | null;
  quantity: number;
  capital: Money;
  expected_profit: Money;
  realized_profit: Money | null;
  days_held: number;
  exit_reason: string;
  is_closed: boolean;
}

/**
 * One candidate refused for one reason, across every day it happened.
 *
 * Grouped by the API rather than here: the response is capped, so counting a
 * truncated list client-side would report a number that is quietly wrong.
 */
export interface BacktestRefusal {
  opportunity_id: string;
  title: string;
  reason_code: string;
  detail: string;
  days: number;
  first_at: string;
  last_at: string;
}

export interface BacktestResult {
  window: { from: string | null; to: string | null; days: number };
  policy_version: string;
  exit_rule: string;
  starting_capital: Money;
  summary: string;
  results: {
    positions_opened: number;
    positions_closed: number;
    capital_committed: Money;
    realized_profit: Money;
    realized_roi: Money | null;
    win_rate: Money | null;
    worst_trade: Money | null;
    prediction_accuracy: Money | null;
  };
  evidence: {
    observations_used: number;
    simulated_share: Money;
    skipped: { opportunity_id: string; title: string; reason: string }[];
  };
  trades: BacktestTrade[];
  refusals: BacktestRefusal[];
  caveats: string[];
}

export type SellAction = "hold" | "sell_now" | "reprice" | "liquidate";

export interface SellRecommendation {
  position_id: string;
  action: SellAction;
  summary: string;
  reasons: string[];
  floor_price: Money | null;
  current_price: Money | null;
  days_held: number;
  remaining_units: number;
  capital_at_risk: Money;
  is_urgent: boolean;
}

export interface SellReview {
  items: SellRecommendation[];
  total: number;
  by_action: Record<string, number>;
  needs_attention: number;
  capital_needing_attention: Money;
  note: string;
}

export interface LearningFinding {
  dimension: string;
  segment: string;
  sample: number;
  direction: "overestimated" | "underestimated";
  mean_signed_error: Money;
  mean_absolute_error: Money;
  agreement: Money;
  capital_involved: Money;
  predicted_total: Money;
  actual_total: Money;
  impact: Money;
  summary: string;
  suggestion: string;
}

export interface LearningReport {
  observations: number;
  summary: string;
  minimum_sample: number;
  findings: LearningFinding[];
  insufficient: { dimension: string; segment: string; sample: number; reason: string }[];
}

export const historyApi = {
  dataset: () => api.get<DatasetTotals>("/history"),
  universe: () =>
    api.get<{ items: TrackedListing[]; total: number; due_now: number }>(
      "/history/universe?limit=200",
    ),
  listing: (id: string, query = "") =>
    api.get<{
      listing: {
        id: string;
        marketplace: string;
        external_id: string;
        title: string;
        provider: string | null;
      };
      coverage: ObservationCoverage;
      statistics: PriceHistory;
      observations: Record<string, unknown>[];
    }>(`/history/listings/${id}${query}`),
  track: (body: {
    marketplace: string;
    external_id: string;
    priority?: number;
    refresh_interval_seconds?: number | null;
    notes?: string;
  }) => api.post<TrackedListing>("/history/universe", body),
  untrack: (id: string) => api.del<TrackedListing>(`/history/universe/${id}`),
  refresh: () =>
    api.post<{ polled: number; observations: number; failures: number }>("/history/refresh"),
};

// ---------------------------------------------------------------- auth

export interface SessionSummary {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  user_agent: string | null;
  /** The session this request arrived on. Ending it signs you out. */
  is_current: boolean;
}

export interface Account {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  organization_id: string;
  last_login_at: string | null;
  must_set_password: boolean;
}

export const authApi = {
  me: () => api.get<{ user: Account; sessions: SessionSummary[] }>("/auth/me"),
  login: (body: { email: string; password: string }) =>
    api.post<{ user: Account; expires_at: string; token: string }>("/auth/login", body),
  logout: () => api.post<{ status: string }>("/auth/logout"),
  changePassword: (body: { current_password?: string; new_password: string }) =>
    api.post<{ status: string }>("/auth/password", body),
  revokeSession: (id: string) =>
    api.post<{ status: string; id: string }>(`/auth/sessions/${id}/revoke`),
};

// ----------------------------------------------------------- execution

export type InstructionStatus =
  | "pending"
  | "executed"
  | "partial"
  | "not_executed"
  | "expired"
  | "cancelled";

/**
 * What a person has been asked to buy, and what they actually did.
 *
 * Spreadline decides what, how many and at what maximum price; placing the
 * retail order is a human action. This is the record on both sides of that.
 */
export interface ExecutionInstruction {
  id: string;
  status: InstructionStatus;
  executor: string;
  title: string | null;
  decision_id: string | null;
  position_id: string | null;
  opportunity_id: string | null;
  purchase_id: string | null;
  source_marketplace: string | null;
  source_external_id: string | null;
  source_url: string | null;
  quantity_authorized: number;
  max_unit_price: Money;
  capital_authorized: Money;
  expires_at: string | null;
  is_expired: boolean;
  quantity_executed: number;
  unit_price_paid: Money | null;
  capital_spent: Money | null;
  executed_at: string | null;
  executed_by: string | null;
  order_reference: string | null;
  /** Stable codes for how the execution departed from the authorisation. */
  variances: string[];
  notes: string | null;
  created_at: string;
  summary: string;
}

export interface ExecutionReport {
  summary: string;
  outstanding: ExecutionInstruction[];
  recent: ExecutionInstruction[];
  counts: {
    pending: number;
    closed: number;
    with_variance: number;
    by_variance: Record<string, number>;
  };
  note: string;
}

export const executionApi = {
  overview: () => api.get<ExecutionReport>("/execution"),
  record: (
    id: string,
    body: {
      quantity: number;
      unit_price?: string;
      order_reference?: string;
      shipping_cost?: string;
      tax?: string;
      other_costs?: string;
      notes?: string;
    },
  ) => api.post<ExecutionInstruction>(`/execution/instructions/${id}/record`, body),
  cancel: (id: string, body: { reason: string }) =>
    api.post<ExecutionInstruction>(`/execution/instructions/${id}/cancel`, body),
};

// ------------------------------------------------------- manual entry

/** One side of a hand-entered pair, as a person reads it off the page. */
export interface ManualSideInput {
  marketplace: string;
  external_id: string;
  title: string;
  price: string;
  url?: string;
  brand?: string;
  model?: string;
  category?: string;
  shipping?: string;
  /** gtin, upc, ean, isbn, asin, mpn or model. Keyed on both sides to match. */
  identifiers?: Record<string, string>;
  /** Absent rather than zero when unknown, so nothing is guessed at. */
  sales_rank?: number | null;
  seller_count?: number | null;
  review_count?: number | null;
  note?: string;
}

export interface ManualEntryReport {
  method: "manual";
  shared_identifiers: string[];
  /** What this pair cannot answer, said before the verdict rather than inside it. */
  gaps: string[];
}

/** How sure the parser is about one field it read off a pasted page. */
export type PasteConfidence = "certain" | "likely" | "guess";

export interface PasteField {
  name: string;
  value: string;
  confidence: PasteConfidence;
  /** The line it was read from, so a wrong value is traceable to the text. */
  evidence: string;
  /** Readings that were rejected. Present where the parser had to choose. */
  alternatives: string[];
}

export interface PasteDraft {
  marketplace: string | null;
  /** False for a home page or a search results page: many products, no subject. */
  is_product_page: boolean;
  summary: string;
  fields: PasteField[];
  problems: string[];
  identifiers_seen: number;
  note: string;
}
