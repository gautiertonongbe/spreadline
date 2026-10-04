"use client";

/**
 * Typing in a pair you looked up yourself.
 *
 * Two columns because that is what the person is looking at: one browser tab per
 * side. The fields are ordered the way you meet them on a product page rather
 * than the way the API takes them, so filling this in is reading left to right
 * down a page instead of hunting.
 *
 * Four fields per side are required. Everything else is folded away, because a
 * form that demands twelve fields before it will do anything gets abandoned, and
 * the two that matter most are called out by name rather than left to be
 * discovered in a refusal: the identifier, which is what lets the match clear a
 * policy threshold, and the category, which is what the referral fee is keyed on.
 */

import { useState } from "react";

import { Button, Card, Disclosure, ErrorState, Note } from "@/components/ui";
import {
  endpoints,
  type AnalysisResponse,
  type ManualEntryReport,
  type PasteDraft,
  type PasteField,
} from "@/lib/api";

type Side = {
  marketplace: string;
  external_id: string;
  title: string;
  price: string;
  shipping: string;
  brand: string;
  category: string;
  identifierKind: string;
  identifierValue: string;
  url: string;
  sales_rank: string;
  seller_count: string;
};

const EMPTY: Side = {
  marketplace: "walmart",
  external_id: "",
  title: "",
  price: "",
  shipping: "0",
  brand: "",
  category: "",
  identifierKind: "upc",
  identifierValue: "",
  url: "",
  sales_rank: "",
  seller_count: "",
};

const MARKETPLACES = ["walmart", "amazon", "ebay", "bestbuy"];
const IDENTIFIERS = ["upc", "ean", "gtin", "asin", "mpn", "model", "isbn"];

function toInput(side: Side) {
  const identifiers: Record<string, string> = {};
  if (side.identifierValue.trim()) {
    identifiers[side.identifierKind] = side.identifierValue.trim();
  }
  return {
    marketplace: side.marketplace,
    external_id: side.external_id.trim(),
    title: side.title.trim(),
    price: side.price.trim(),
    shipping: side.shipping.trim() || "0",
    brand: side.brand.trim() || undefined,
    category: side.category.trim() || undefined,
    url: side.url.trim() || undefined,
    identifiers,
    // Blank means unknown, and unknown is reported as no evidence rather than
    // sent as a zero the engine would read as a fact.
    sales_rank: side.sales_rank.trim() ? Number(side.sales_rank) : null,
    seller_count: side.seller_count.trim() ? Number(side.seller_count) : null,
  };
}

function field(
  label: string,
  value: string,
  onChange: (next: string) => void,
  extra?: { placeholder?: string; numeric?: boolean; id?: string },
) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input
        id={extra?.id}
        value={value}
        inputMode={extra?.numeric ? "decimal" : undefined}
        placeholder={extra?.placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={`mt-1 w-full rounded border border-border bg-canvas px-2.5 py-[7px] text-xs text-primary outline-none transition focus:border-accent/50 ${
          extra?.numeric ? "numeric" : ""
        }`}
      />
    </label>
  );
}

const CONFIDENCE_TONE: Record<string, string> = {
  certain: "text-buy",
  likely: "text-secondary",
  guess: "text-review",
};

const CONFIDENCE_WORD: Record<string, string> = {
  certain: "read from a labelled row",
  likely: "matched in context",
  guess: "picked from candidates",
};

/**
 * Paste the page instead of typing it.
 *
 * Copying text off a page you are looking at is not automated access: nothing
 * fetches, and it is the same act as typing, only faster. What changes is
 * certainty, so nothing here fills a field silently. The draft says what it read
 * and how sure it is, fills the form, and leaves the confirming to the person.
 */
function PasteBox({
  marketplace,
  onDraft,
}: {
  marketplace: string;
  onDraft: (draft: PasteDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<PasteDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = async (value: string) => {
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body = await endpoints.readPaste({ text: value, marketplace });
      setDraft(body);
      if (body.is_product_page) {
        onDraft(body);
        setOpen(false);
        setText("");
      }
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      {!open ? (
        <Button size="small" onClick={() => setOpen(true)}>
          Paste the page instead
        </Button>
      ) : (
        <div className="space-y-2">
          <textarea
            value={text}
            rows={4}
            autoFocus
            placeholder="Open one product's own page, select all, copy, paste here. Paste the address bar in too: the item number is in the address and not in the page."
            onChange={(event) => setText(event.target.value)}
            onPaste={(event) => {
              const pasted = event.clipboardData.getData("text");
              if (pasted) {
                event.preventDefault();
                setText(pasted);
                void read(pasted);
              }
            }}
            className="w-full rounded border border-border bg-canvas px-2.5 py-2 text-xs text-primary outline-none transition focus:border-accent/50"
          />
          <div className="flex gap-2">
            <Button
              size="small"
              tone="accent"
              disabled={busy || !text.trim()}
              onClick={() => void read(text)}
            >
              {busy ? "Reading" : "Read it"}
            </Button>
            <Button size="small" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {error && <ErrorState message={error} />}

      {draft && !draft.is_product_page && (
        <Note tone="warning">
          {draft.summary}
          {/* The refusal is the summary and is also problems[0], because an API
              caller reading only one of the two should still learn why. Here
              both are on screen, so show the reason once and then whatever the
              draft has to add to it. */}
          {draft.problems
            .filter((problem: string) => problem !== draft.summary)
            .map((problem: string) => (
              <div key={problem} className="mt-1 text-2xs">
                {problem}
              </div>
            ))}
        </Note>
      )}

      {draft && draft.is_product_page && draft.fields.length > 0 && (
        <div className="rounded border border-border bg-canvas px-3 py-2">
          <div className="label">What it read</div>
          <div className="mt-1.5 space-y-1">
            {draft.fields.map((item: PasteField) => (
              <div key={item.name} className="flex flex-wrap items-baseline gap-x-2 text-2xs">
                <span className="text-faint">{item.name.replace(/_/g, " ")}</span>
                <span className="text-secondary">{item.value}</span>
                <span
                  className={CONFIDENCE_TONE[item.confidence] ?? "text-muted"}
                  title={item.evidence}
                >
                  {CONFIDENCE_WORD[item.confidence] ?? item.confidence}
                </span>
              </div>
            ))}
          </div>
          {draft.problems.length > 0 && (
            <div className="mt-2 space-y-1">
              {draft.problems.map((problem: string) => (
                <div key={problem} className="text-2xs leading-relaxed text-review">
                  {problem}
                </div>
              ))}
            </div>
          )}
          <p className="mt-2 text-2xs leading-relaxed text-faint">
            Check these before analysing. A parsed field is a guess about what you were
            looking at, not a statement that you were. The pasted text is not stored.
          </p>
        </div>
      )}
    </div>
  );
}

/** Lay a parsed draft over a side, leaving anything it did not find alone. */
function applyDraft(side: Side, draft: PasteDraft): Side {
  const value = (name: string) =>
    draft.fields.find((item) => item.name === name)?.value ?? "";
  const next = { ...side };

  for (const [field, name] of [
    ["title", "title"],
    ["external_id", "external_id"],
    ["price", "price"],
    ["brand", "brand"],
    ["category", "category"],
    ["sales_rank", "sales_rank"],
    ["seller_count", "seller_count"],
  ] as const) {
    const found = value(name);
    if (found) next[field] = found;
  }

  // The identifier the page actually carried, in the order the matcher prefers.
  for (const kind of ["upc", "ean", "gtin", "isbn", "mpn", "model"]) {
    const found = value(kind);
    if (found) {
      next.identifierKind = kind;
      next.identifierValue = found;
      break;
    }
  }
  if (draft.marketplace) next.marketplace = draft.marketplace;
  return next;
}

function SideForm({
  heading,
  hint,
  side,
  onChange,
  isExit,
}: {
  heading: string;
  hint: string;
  side: Side;
  onChange: (next: Side) => void;
  isExit: boolean;
}) {
  const set = (key: keyof Side) => (value: string) => onChange({ ...side, [key]: value });
  const prefix = isExit ? "exit" : "source";

  return (
    <div className="space-y-3">
      <div>
        <h3 className="display text-[0.9375rem] font-medium text-primary">{heading}</h3>
        <p className="mt-0.5 text-2xs leading-relaxed text-muted">{hint}</p>
      </div>

      <PasteBox
        marketplace={side.marketplace}
        onDraft={(draft) => onChange(applyDraft(side, draft))}
      />

      <label className="block">
        <span className="label">Marketplace</span>
        <select
          id={`${prefix}-marketplace`}
          value={side.marketplace}
          onChange={(event) => set("marketplace")(event.target.value)}
          className="mt-1 w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
        >
          {MARKETPLACES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>

      {field("Product title", side.title, set("title"), {
        placeholder: "copy it from the page",
        id: `${prefix}-title`,
      })}

      <div className="grid gap-3 sm:grid-cols-2">
        {field("Item or ASIN", side.external_id, set("external_id"), {
          placeholder: isExit ? "B07FDJMC9Q" : "whatever the URL ends in",
          id: `${prefix}-external-id`,
        })}
        {field("Price", side.price, set("price"), {
          numeric: true,
          placeholder: "0.00",
          id: `${prefix}-price`,
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-[110px_1fr]">
        <label className="block">
          <span className="label">Identifier</span>
          <select
            id={`${prefix}-identifier-kind`}
            value={side.identifierKind}
            onChange={(event) => set("identifierKind")(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-canvas py-[7px] pl-2.5 text-xs text-primary"
          >
            {IDENTIFIERS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        {field("Number", side.identifierValue, set("identifierValue"), {
          placeholder: "the barcode, same on both sides",
          id: `${prefix}-identifier-value`,
        })}
      </div>

      <Disclosure title="More detail, all optional">
        <div className="space-y-3 pt-1">
          <div className="grid gap-3 sm:grid-cols-2">
            {field("Category", side.category, set("category"), {
              placeholder: "home & kitchen",
              id: `${prefix}-category`,
            })}
            {field("Brand", side.brand, set("brand"), { id: `${prefix}-brand` })}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {field("Shipping", side.shipping, set("shipping"), {
              numeric: true,
              id: `${prefix}-shipping`,
            })}
            {field("Link", side.url, set("url"), {
              placeholder: "https://",
              id: `${prefix}-url`,
            })}
          </div>
          {isExit && (
            <div className="grid gap-3 sm:grid-cols-2">
              {field("Best sellers rank", side.sales_rank, set("sales_rank"), {
                numeric: true,
                placeholder: "leave blank if unknown",
                id: `${prefix}-sales-rank`,
              })}
              {field("Sellers on the listing", side.seller_count, set("seller_count"), {
                numeric: true,
                placeholder: "leave blank if unknown",
                id: `${prefix}-seller-count`,
              })}
            </div>
          )}
        </div>
      </Disclosure>
    </div>
  );
}

export function ManualEntry({
  onAnalysed,
}: {
  onAnalysed: (analysis: AnalysisResponse, entry: ManualEntryReport) => void;
}) {
  const [source, setSource] = useState<Side>({ ...EMPTY, marketplace: "walmart" });
  const [target, setTarget] = useState<Side>({ ...EMPTY, marketplace: "amazon" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready =
    source.title.trim() &&
    source.external_id.trim() &&
    source.price.trim() &&
    target.title.trim() &&
    target.external_id.trim() &&
    target.price.trim() &&
    source.marketplace !== target.marketplace;

  const matched =
    source.identifierValue.trim() &&
    target.identifierValue.trim() &&
    source.identifierKind === target.identifierKind;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = await endpoints.analyzeManual({
        source: toInput(source),
        target: toInput(target),
      });
      onAnalysed(body, body.entry);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Note>
        Nothing here calls anything. You read two public pages and tell the app what was
        on them, which needs no key and nobody&apos;s permission, and the same engine then
        judges it by the same standard it judges an API-fed pair by. Paste the page and
        most of it fills itself in; check what it read, because a parsed field is a guess
        about what you were looking at. Do the same pair again next week and you have a
        price history nobody had to be granted.
      </Note>

      <Card>
        <div className="grid gap-7 lg:grid-cols-2">
          <SideForm
            heading="Where you would buy"
            hint="The retail listing you are looking at."
            side={source}
            onChange={setSource}
            isExit={false}
          />
          <SideForm
            heading="Where you would sell"
            hint="The listing for the same item on the exit market."
            side={target}
            onChange={setTarget}
            isExit
          />
        </div>

        {!matched && (
          <div className="mt-5">
            <Note tone="warning">
              Put the same barcode on both sides. Without it the match can only be made
              on the titles, the identity ladder caps that at 60%, and the pair will be
              analysed and then refused for low confidence. It is ten seconds and it is
              the difference between an answer and a refusal.
            </Note>
          </div>
        )}

        {error && (
          <div className="mt-4">
            <ErrorState message={error} />
          </div>
        )}

        <div className="mt-5 flex items-center gap-3">
          <Button tone="accent" disabled={busy || !ready} onClick={() => void submit()}>
            {busy ? "Working it out" : "What would I make?"}
          </Button>
          {source.marketplace === target.marketplace && (
            <span className="text-2xs text-review">
              Buying and selling in the same market is a round trip, not a spread.
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}

/** What the entry could not answer, shown above the verdict rather than inside it. */
export function ManualGaps({ entry }: { entry: ManualEntryReport }) {
  if (!entry.gaps.length) return null;
  return (
    <Note tone="warning">
      <div className="font-medium">What you left out, and what it cost</div>
      <ul className="mt-1.5 space-y-1">
        {entry.gaps.map((gap) => (
          <li key={gap} className="flex gap-2">
            <span className="text-faint">·</span>
            <span>{gap}</span>
          </li>
        ))}
      </ul>
    </Note>
  );
}
