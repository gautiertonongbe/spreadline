# Spreadline

Cross-market inventory intelligence.

Spreadline does not merely find price differences. It determines whether a spread
represents a good inventory decision, and it says why.

```
source -> product -> marketplace -> price -> demand -> competition
       -> risk -> capital -> outcome
```

## Quick start

No database server and no credentials are needed for a first run.

```bash
make install      # virtualenv, backend dependencies, frontend dependencies
make seed         # migrate a local SQLite database and analyse 36 fixture products
make account      # create the first sign-in (prompts for a password)
make api          # API on http://localhost:8000  (docs at /docs)
make web          # UI on http://localhost:3000
```

There is no default password and no public sign-up: the first account is created
from the command line. See [docs/security.md](docs/security.md).

Or the whole stack against Postgres:

```bash
cp .env.example .env
make up           # docker compose: postgres, api, web
```

`make check` runs the lint and the test suite, which is what CI would run.
`make e2e` drives the browser against a running stack, and `make providers` calls
every configured data provider for real.

## What it does

Given a product on one marketplace and a counterpart on another, Spreadline runs
the workflow in one pass and stores every artefact behind the answer:

1. **Identity.** Resolves whether the two listings are genuinely the same
   sellable product, through an identifier ladder (GTIN, UPC, EAN, MPN, brand and
   model, attributes, title) with a variation engine on top that detects pack,
   size, capacity, colour, model, generation, condition, bundle and accessory
   differences.
2. **Economics.** Computes the true unit economics deterministically: referral
   fee, fulfilment, storage, returns, inbound shipping, prep, tax, plus
   break-even price and the maximum you can pay per unit.
3. **Price intelligence.** Builds its own history and reports statistics per
   window, each with the observation count, day coverage and confidence behind it.
4. **Demand and competition.** Measures both on the *exit* market, since that is
   where the unit has to sell.
5. **Risk.** Collects named signals, each with a severity and its evidence.
6. **Stress test.** Re-prices the unit under seven scenarios.
7. **Score and decision.** A configurable weighted score, then BUY, REVIEW or
   PASS, with the gates and reasons that produced it.

Above that sits a policy layer that decides how much capital may move and under
what conditions, an allocator that chooses between candidates rather than
checking them one at a time ("I have $500 and eleven things that clear the
policy; which ones, and how many of each?"), and a replay that answers the
question worth asking before any of it: given only what was recorded at the
time, what would these limits have done over the last 90 days, and what would
have happened.

An authorised decision then produces an instruction a person acts on. Spreadline
does not place retail orders, and recording what you actually paid is what keeps
every number afterwards honest: a position left at the figures that were
authorised makes every realised return a measurement of a purchase that never
happened. See [docs/autonomy.md](docs/autonomy.md).

## The rules the code actually enforces

These are the claims worth checking, because they are what separate this from a
spread calculator.

**Money is deterministic.** Every financial figure is `Decimal`, computed by one
engine. No model, heuristic or provider ever produces money. Line items always
reconcile to the headline figure; there is a test for exactly that.

**Assumptions are frozen.** Every profitability snapshot embeds the full fee
assumption set and its fingerprint. Publishing a fee change today cannot rewrite
what a prediction made last month was based on.

**Missing data stays missing.** Demand with no observations is confidence `none`,
never a score of zero. A price window without the observations *and* day-span to
support a statistic returns `null` with the reason, not a number. ROI against zero
invested capital is `null`, not 0%. The UI renders all of these as "not available".

**Title similarity can never confirm a match.** "Sony WH-1000XM5" and
"Sony WH-1000XM4" are 93% similar and are different products. A title-only match
is capped below the high-confidence band, and a blocking variation conflict
(pack count, capacity, size, model, accessory, condition, generation) rejects the
match regardless of how strong the other evidence looked.

**A candidate that cannot be bought scores zero.** A games console matched against
its own carrying case shows a $265 apparent profit. Without this rule it would
sort to the top of the opportunity table and read as the best idea on the screen.

**A low price is a question, not an opportunity.** Clearance, a pricing error and
a genuine promotion look identical in a price series. The anomaly detector reports
what it sees, and a source priced like clearance routes to REVIEW even when the
economics are excellent.

**Fixture data is labelled as fixture data**, on every response and in the UI.

## Repository

```
backend/
  app/
    core/        configuration, database, money, time, logging, errors, security
    models/      23 tables, multi-tenant from day one
    domains/     catalog, identity, pricing, demand, competition, profitability,
                 quality, risk, opportunities, portfolio, validation, analytics
    services/    providers (base, mock, bestbuy, ebay, amazon, walmart,
                 platforms catalogue, reliability, registry), scheduling, ai
    api/         routes
  tests/         297 tests
  migrations/    alembic
  scripts/       seed
frontend/        Next.js, TypeScript, Tailwind
docs/            architecture, data model, providers, scoring, decisions
```

## Providers

Nothing above the provider layer knows which marketplace or vendor data came
from. Providers declare their capabilities, and a provider that cannot answer
raises rather than returning an empty result, because "no demand data" and
"demand of zero" are different facts.

| Platform | State | What it takes |
| --- | --- | --- |
| **Best Buy** | **Live** | Free developer key, no seller account. Returns UPC and shipping weight. |
| **eBay** | **Live** | Free developer account, 5,000 calls/day. Supports GTIN lookup. |
| Amazon | Adapter ready | Mappers implemented and tested; transport unwired. Needs a seller account. |
| Walmart | Adapter ready | Mappers implemented and tested; transport unwired. Needs a seller account. |
| `mock_amazon`, `mock_walmart` | Fixture | 36 fixture products, deterministic, offline |
| Keepa, Rainforest, BlueCart, SerpApi | Planned | Data feeds. Registered with their credential and cost; not implemented. |
| Shopify, Etsy, Target, Home Depot | Planned | Marketplaces and retailers. Registered; not implemented. |
| Faire, Alibaba | Planned | Wholesale and distributor sourcing. Registered; not implemented. |

`GET /api/v1/platforms` and the Providers screen list all sixteen with their
status, capabilities, required credentials and cost, so "what would it take to
connect X" is answered by the running system rather than by a roadmap document.

**To use real data today, for free**, no seller account and no subscription:

```bash
# https://developer.bestbuy.com  and  https://developer.ebay.com
BESTBUY_API_KEY=...
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
ENABLED_PROVIDERS=free
```

Best Buy is the source and eBay the exit market. Both are official APIs, so
nothing here scrapes. The registry prefers a configured live provider over a
fixture automatically, so no code changes.

A platform that is registered but not implemented **refuses every call** with the
reason. It never returns an empty result, because a silent empty is
indistinguishable from a real one, and "no offers" changes a decision.

The Amazon and Walmart adapters ship with real, tested payload mappers and a
transport that raises `ProviderNotConfiguredError` until credentials exist. This
is deliberate: a fabricated live integration is worse than an absent one, because
it produces numbers that look like market data.

Every call is wrapped in timeouts, bounded retries with exponential backoff and
full jitter, a token-bucket rate limiter, a circuit breaker and health tracking.
Only transport-shaped failures are retried; a capability error or a missing
credential is a permanent answer.

### Real Amazon products, from the one open source

There is no open source of live Amazon prices: PA-API v5 is retired and answers
403, the Creators API that replaced it needs 10 qualifying affiliate sales in 30
days, SP-API needs a seller account, and the aggregators are paid. The
**Amazon Reviews 2023** dataset is genuinely open, and the importer treats it
accordingly:

```bash
# raw_meta_<Category> from
# https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023
python -m scripts.import_amazon_dataset meta_Electronics.jsonl.gz --dry-run
python -m scripts.import_amazon_dataset meta_Electronics.jsonl.gz --limit 5000
```

Every observation carries the **2023 crawl date**, never today, and
`source="dataset"`. A three-year-old price stamped as current would set the
reference median and make the anomaly detector measure live prices against it.
Because the timestamp is honest, the freshness logic marks these rows stale
without being told to. Good for a realistic catalogue and for backtesting; not
market data.

## Is it using real data?

```bash
make providers     # calls every configured provider for real and reports what answered
```

Out of the box: **no**. The two fixture providers serve the 36-product catalogue,
and every price is labelled as fixture data on every response and in the UI. That
is a deliberate state, not an oversight: a provider without credentials refuses
rather than silently falling back, so a real label never appears over invented
numbers.

Three ways to get real data, in order of effort:

**1. The open Amazon dataset. No signup, works now.**

```bash
curl -L -o meta_Gift_Cards.jsonl \
  https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/raw/meta_categories/meta_Gift_Cards.jsonl
python -m scripts.import_amazon_dataset meta_Gift_Cards.jsonl
```

Real ASINs, titles, ratings and prices, dated to the 2023 crawl and recorded as
real observations with honest timestamps. The freshness logic marks them stale on
their own. Good for a realistic catalogue and for backtesting; not current prices.
`meta_Gift_Cards` is 1.9 MB; larger categories run to hundreds of MB.

**2. Live prices, free, no seller account.** Two free developer keys. Neither is
issued on the spot: Best Buy reviews the key request, and eBay reviews the
developer account itself before any keyset can be created, which takes at least a
business day. Apply to both in parallel, because the queues do not run in series:

```bash
BESTBUY_API_KEY=...        # https://developer.bestbuy.com
EBAY_CLIENT_ID=...         # https://developer.ebay.com
EBAY_CLIENT_SECRET=...
ENABLED_PROVIDERS=free
```

Then `make providers` calls both for real and prints what came back. Best Buy is
the source and eBay the exit market; both are official APIs.

**3. Amazon and Walmart live.** Both need a Professional seller account. The
payload mappers are written and tested; only the transport is unwired.

## Testing

```bash
make test
```

568 backend tests, plus a Playwright suite that drives the flows in a browser
(`make e2e`). The fixture catalogue is built around the decisions that are expensive
to get wrong: false matches, pack and size mismatches, a price that is low because
the stock is about to run out, a product with three observations pretending to
have a trend, and a spread that fees erase entirely.

## Validation before trust

The platform measures itself. Record what you actually found on a product with
"Record a hand check", and the dashboard reports match accuracy, price accuracy,
BUY precision and false positive rate, each with the denominator it was computed
from. Below 150 hand-checked products those rates are labelled as not yet
meaningful, because they are not.

## Not built, on purpose

No automated purchasing, checkout, fulfilment, listing or repricing: the retail
order is placed by a person, and the product is built around recording what they
did rather than around doing it for them. No CAPTCHA handling and no
unauthorised scraping. No Kubernetes, microservices, Kafka, Redis
or GraphQL. The complexity belongs in the intelligence and the data model, not in
the infrastructure.

## Documentation

- [docs/architecture.md](docs/architecture.md)
- [docs/data-model.md](docs/data-model.md)
- [docs/providers.md](docs/providers.md)
- [docs/scoring.md](docs/scoring.md)
- [docs/decisions.md](docs/decisions.md)
- [docs/evidence.md](docs/evidence.md)
- [docs/history.md](docs/history.md)
- [docs/autonomy.md](docs/autonomy.md)
- [docs/security.md](docs/security.md)
- [docs/interface.md](docs/interface.md)
