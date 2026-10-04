# Architecture

## Shape

A modular monolith. One deployable, organised by business domain, with the seams
drawn where a service boundary would go if one is ever needed.

```
                        DATA SOURCES
        Amazon            Walmart            Other
           |                 |                 |
           +--------- PROVIDER LAYER ----------+
                            |
                    NORMALISATION            (domains/catalog)
                            |
                  PRODUCT IDENTITY           (domains/identity)
                            |
          +-----------------+-----------------+
          |                                   |
   MARKETPLACE DATA                     PRODUCT GRAPH
   pricing / demand / competition       products, listings, offers
          |                                   |
          +-----------------+-----------------+
                            |
                     PROFITABILITY            (domains/profitability)
                            |
                         RISK                 (domains/risk)
                            |
                      OPPORTUNITY             (domains/opportunities)
                            |
                       DECISION
                            |
                  CAPITAL ALLOCATION          (domains/portfolio)
                            |
                   +--------+--------+
                   |                 |
                  UI                API
                   |                 |
                   +--------+--------+
                            |
                    OUTCOME TRACKING          (domains/portfolio, validation)
                            |
                  ANALYTICS / LEARNING        (domains/analytics)
```

Dependencies point one way: `api` depends on `domains`, `domains` depend on
`models` and `core`, and `services/providers` is depended upon but depends on
nothing above it. No domain imports another domain's persistence.

## Why a monolith

The hard parts of this product are the data model and the reasoning, not the
topology. Splitting identity resolution, pricing and profitability into services
would add network failure modes between steps that must agree with each other,
and would make the single most important property harder to hold: that one
analysis produces one internally consistent answer.

Extraction stays cheap because each domain is already a package with its own
entry points and no shared mutable state.

## The analysis pipeline

`domains/opportunities/analysis.py` is the orchestrator, and the ordering is
load-bearing:

1. **Ingest the source listing.** Upsert the listing, resolve the canonical
   product, snapshot the current price.
2. **Enrich.** Offers, price history, demand and competition, each attempted
   independently. One capability failing must not cost the others; what is
   missing lands in the data-quality score instead.
3. **Find the counterpart.** Identifier lookup first. Title search is the
   fallback and is accepted only above a similarity floor, because a wrong
   counterpart poisons every downstream engine with another product's data.
4. **Resolve identity.** Before any economics, so a rejected match never produces
   a profit figure someone could act on.
5. **Build the context.** One object carrying every engine's output.
6. **Evaluate.** Risk, then score, then decision, then stress test.
7. **Persist.** Opportunity, profitability snapshot with frozen assumptions, risk
   assessment with its signals, and a lifecycle event.

Steps 5 to 7 take a context and nothing else, which is what makes the same code
usable for a live analysis, a fixture run and a historical reconstruction.

## Layers

**core** owns configuration, the engine and session, money, time, logging,
errors and security primitives. Nothing else reads `os.environ`, constructs a
`Decimal` from a float, or calls `datetime.now()`.

**models** is the schema. Every tenant-owned table carries `organization_id` from
day one. Enums are stored as short strings so a new risk code or lifecycle state
ships without a migration.

**domains** hold the business logic. Each is a package with pure functions over
dataclasses at its core and a thin `service.py` where persistence is needed. The
engines are pure: `calculate_profitability`, `match_listings`, `assess_risk`,
`score_opportunity`, `decide` and `allocate_capital` all take values and return
values, which is why they are cheap to test exhaustively.

**services/providers** is the outside world. See [providers.md](providers.md).

**api** is transport only. Routes validate, call a domain, and serialise. No
business logic lives in a route.

## Money

Money is `Decimal` throughout, stored at `Numeric(14, 4)` and presented at two
places. Floats are never used, and `to_decimal` routes floats through `str` so a
stray `0.1` cannot arrive as `0.1000000000000000055`.

Intermediate precision is four places on purpose: per-unit fees are frequently
fractions of a cent, and rounding them at every step produces drift that surfaces
as unexplained pennies in a settlement reconciliation.

Across the API boundary money is a **string**. A JSON number becomes a float in
every JavaScript client.

## Multi-tenancy

One organization today. The schema, every query and the `AuthContext` are already
multi-tenant. `get_auth` in `api/deps.py` is the single place that decides who is
acting; adding authentication means replacing that function's body, not auditing
every route.

## Time and backtesting

Every timestamp is timezone-aware UTC and every wall-clock read goes through
`core/clock.utcnow()`. Observation tables are append-only. Together these are
what make it possible to ask "what did we know at time T" without look-ahead
bias, and `domains/backtest` is what asks it: every analysis loader takes an
`as_of` and truncates to it, so a replay sees exactly what was recorded by the
simulated day and nothing later. See `docs/autonomy.md` for what a replay does
and does not claim.

## Scheduling

`services/scheduling` defines a `Scheduler` interface with an APScheduler
implementation and a no-op used in tests. Jobs are registered unconditionally and
the scheduler decides whether to run them, so the registered job list never
drifts from what actually runs. Moving to Celery or Temporal is a new subclass.

## Observability

Structured logs with credential redaction at the formatter, so a provider key in
a URL or an exception repr cannot reach a log sink. Every provider call is
recorded with its capability, latency, attempt count and outcome, buffered in
memory and flushed after the request rather than inside it.
