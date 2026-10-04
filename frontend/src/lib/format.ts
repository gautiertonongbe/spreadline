/**
 * Formatting.
 *
 * Every function here takes a nullable decimal string and returns a string.
 * ``null`` is rendered as an em-free "not available" marker rather than as a
 * zero, because the platform's central claim is that it distinguishes the two.
 */

export const NOT_AVAILABLE = "not available";

export function money(value: string | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || value === "") return NOT_AVAILABLE;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return NOT_AVAILABLE;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numeric);
}

/** Compact money for dense tables: $1.2k rather than $1,234.00. */
export function moneyCompact(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return NOT_AVAILABLE;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return NOT_AVAILABLE;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: Math.abs(numeric) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(numeric) >= 10_000 ? 1 : 2,
  }).format(numeric);
}

export function percent(
  value: string | number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || value === "") return NOT_AVAILABLE;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return NOT_AVAILABLE;
  return `${(numeric * 100).toFixed(digits)}%`;
}

export function score(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return NOT_AVAILABLE;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return NOT_AVAILABLE;
  return numeric.toFixed(0);
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_AVAILABLE;
  return new Intl.NumberFormat("en-US").format(value);
}

export function date(value: string | null | undefined): string {
  if (!value) return NOT_AVAILABLE;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return NOT_AVAILABLE;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

/** Below this, a future timestamp is clock skew between machines, not an error. */
const SKEW_TOLERANCE_SECONDS = 120;

export function relativeDate(value: string | null | undefined): string {
  if (!value) return NOT_AVAILABLE;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return NOT_AVAILABLE;
  const seconds = (Date.now() - parsed.getTime()) / 1000;

  // Nothing that has already happened happened in the future. A second or two
  // ahead is two clocks disagreeing, and reads fine as "just now". Anything
  // further ahead is a real problem somewhere upstream, and it is shown as an
  // absolute date rather than as "in 6 hours", which is the shape this took
  // when timestamps were serialised without a timezone: honest, and legible,
  // without hiding that something is wrong.
  if (seconds < 0) {
    return -seconds <= SKEW_TOLERANCE_SECONDS ? "just now" : date(value);
  }
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [2592000, "day"],
  ];
  const formatter = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
  if (seconds < 60) return formatter.format(-Math.round(seconds), "second");
  for (let index = 1; index < units.length; index += 1) {
    const [limit, unit] = units[index]!;
    const previous = units[index - 1]![0];
    if (seconds < limit) return formatter.format(-Math.round(seconds / previous), unit);
  }
  return formatter.format(-Math.round(seconds / 2592000), "month");
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return NOT_AVAILABLE;
  return value
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

/**
 * Whether a listing URL can actually be opened.
 *
 * Fixture listings carry `https://example.invalid/...`. That is deliberate and
 * correct: `.invalid` is reserved by RFC 2606 precisely so it can never resolve,
 * and inventing a plausible amazon.com link for a product that does not exist
 * would send someone to a real listing for a different item. What was wrong was
 * the interface, which rendered it as a button that looked like it would work
 * and then did nothing at all when clicked.
 */
export function isOpenableUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    // RFC 2606 reserves these for documentation and testing; none of them resolve.
    return !/(^|\.)(invalid|example|test|localhost)$/i.test(parsed.hostname);
  } catch {
    return false;
  }
}

/**
 * The words shown to the reader for each recommendation.
 *
 * The stored taxonomy stays `buy` / `review` / `pass`, because filters, the API
 * and every test are written against it. What a person reads is plainer: nobody
 * sourcing inventory thinks "this candidate is in the review state", they think
 * "check this one first". The mapping lives here so the two never drift.
 */
const RECOMMENDATION_WORDS: Record<string, { short: string; sentence: string }> = {
  buy: { short: "Buy", sentence: "Buy it" },
  review: { short: "Check", sentence: "Check it first" },
  pass: { short: "Skip", sentence: "Skip it" },
};

export function recommendationLabel(
  value: string,
  form: "short" | "sentence" = "short",
): string {
  return RECOMMENDATION_WORDS[value]?.[form] ?? titleCase(value);
}

/** Signed display for a delta, so a negative variance reads unambiguously. */
export function signedMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return NOT_AVAILABLE;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return NOT_AVAILABLE;
  const formatted = money(value);
  return numeric > 0 ? `+${formatted}` : formatted;
}
