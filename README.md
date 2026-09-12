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
make api          # API on http://localhost:8000  (docs at /docs)
make web          # UI on http://localhost:3000
```

Or the whole stack against Postgres:

```bash
cp .env.example .env
make up           # docker compose: postgres, api, web
```

`make check` runs the lint and the test suite, which is what CI would run.

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
    services/    providers (base, mock, amazon, walmart, reliability, registry),
                 scheduling
    api/         routes
  tests/         246 tests
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

| Provider | State | Notes |
| --- | --- | --- |
| `mock_amazon`, `mock_walmart` | Ready | 36 fixture products, deterministic, offline |
| `amazon` | Adapter implemented, transport unwired | Set `AMAZON_PROVIDER_CREDENTIALS` and a base URL |
| `walmart` | Adapter implemented, transport unwired | Set `WALMART_PROVIDER_CREDENTIALS` and a base URL |

The live adapters ship with real, tested payload mappers and a transport that
raises `ProviderNotConfiguredError` until credentials exist. This is deliberate:
a fabricated live integration is worse than an absent one, because it produces
numbers that look like market data. Adding one is an adapter change; nothing
above it moves.

Every call is wrapped in timeouts, bounded retries with exponential backoff and
full jitter, a token-bucket rate limiter, a circuit breaker and health tracking.
Only transport-shaped failures are retried; a capability error or a missing
credential is a permanent answer.

## Testing

```bash
make test
```

246 tests. The fixture catalogue is built around the decisions that are expensive
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

No automated purchasing, checkout, fulfilment, listing or repricing. No CAPTCHA
handling and no unauthorised scraping. No Kubernetes, microservices, Kafka, Redis
or GraphQL. The complexity belongs in the intelligence and the data model, not in
the infrastructure.

## Documentation

- [docs/architecture.md](docs/architecture.md)
- [docs/data-model.md](docs/data-model.md)
- [docs/providers.md](docs/providers.md)
- [docs/scoring.md](docs/scoring.md)
- [docs/decisions.md](docs/decisions.md)
