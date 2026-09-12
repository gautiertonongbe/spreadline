# Data model

23 tables. Every tenant-owned row carries `organization_id`.

## The product graph

```
Product  (canonical, marketplace independent)
  |
  +-- ProductIdentifier   one row per identifier claim, with provenance
  +-- ProductAttribute    normalised attribute, raw value retained
  |
  +-- MarketplaceListing (amazon)
  |     +-- Offer, Offer, Offer          current offer set
  |
  +-- MarketplaceListing (walmart)
        +-- Offer, Offer
```

A product is never "two unrelated URLs". The canonical row is what pricing,
demand, competition and profitability all attach to, and it is what makes
cross-market intelligence possible at all.

**Identifiers are rows, not columns.** A product legitimately has several UPCs
across regions and an id per marketplace, and each claim needs its own source and
validation state. `normalized_value` holds the GTIN-14 form so a UPC-12 and its
EAN-13 equivalent compare equal.

**Attributes keep their raw value** alongside the normalised one. Normalisation
is lossy, and a bug in it is only diagnosable against what the provider actually
said.

## Observations

`price_history`, `demand_history` and `competition_history` are **append-only**.
State is never overwritten, because a statistic must be recomputable and a
backtest must be able to ask what was visible at a past instant.

De-duplication happens at the granularity of the source: live polls by instant,
provider history by calendar day. A provider's historical series returns the same
days but rarely the same timestamps, and matching on the instant would append the
entire history again on every re-analysis, silently doubling the observation
count behind every statistic.

Every demand field is nullable on purpose. A missing sales rank is recorded as
missing; `estimated_monthly_units` is populated only when a provider supplied an
estimate and named its basis.

`competition_history` stores `seller_ids`, not just a count, so entrants and exits
between two observations are a set difference. Three sellers in and three out is a
churning listing, and a net delta of zero hides it.

## Opportunities

```
Opportunity
  +-- OpportunityEvent          append-only lifecycle log
  +-- ProfitabilitySnapshot     base case plus one per stress scenario
  +-- RiskAssessment            level, score, and every signal with its evidence
  +-- OpportunityValidation     the operator's hand check
```

`ProfitabilitySnapshot.assumptions` embeds the **entire** fee assumption set as
JSON, not a foreign key to a mutable assumptions row. A reference would mean that
publishing a fee change today rewrites what every past prediction was based on,
which destroys the accuracy measurement the platform exists to produce.

`score_components` and `explanation` are stored in full, so a stored opportunity
can be explained months later without recomputing anything.

Lifecycle transitions are validated against `OPPORTUNITY_TRANSITIONS`:

```
NEW -> REVIEW | APPROVED | REJECTED | CLOSED
REVIEW -> APPROVED | REJECTED | CLOSED
APPROVED -> PURCHASED | REJECTED | CLOSED
REJECTED -> REVIEW | CLOSED
PURCHASED -> LISTED | CLOSED
LISTED -> SOLD | CLOSED
SOLD -> CLOSED
```

Every transition writes an event, so the history is reconstructible from the log
alone.

## Realised inventory

```
Purchase  -->  Sale
    \           /
     \         /
      Outcome        predicted versus actual
```

`Outcome` snapshots the prediction at the time the purchase was recorded. A later
rescore cannot move the goalposts after the fact.

Cost is attributed to the units that actually sold, so a half-sold position is
`open`, not a loss.

## Providers

`provider_requests` is one row per outbound call: capability, latency, attempts
and outcome, with the target recorded as an identifier and never as a
credential-bearing URL. `provider_health` is one row per provider, updated in
place.

## Portability

Production is PostgreSQL. SQLite is supported so the test suite and a first-run
developer need no database server. Three type decorators absorb the difference:

- `GUID`: native `UUID` on Postgres, `CHAR(36)` elsewhere, always `str` in Python.
- `JSONB`: `JSONB` on Postgres, `JSON` elsewhere.
- `Money` / `Ratio`: `Numeric(14,4)` and `Numeric(12,6)`.

Alembic runs with `render_as_batch` on SQLite so the same migrations apply in both
places. `alembic check` is part of the workflow and reports no drift.

## Table list

| Table | Purpose |
| --- | --- |
| `organizations`, `users`, `user_settings` | Tenancy |
| `products`, `product_identifiers`, `product_attributes` | Canonical product graph |
| `marketplace_listings`, `offers` | Marketplace representation |
| `product_matches` | Identity decisions with evidence and conflicts |
| `price_history`, `demand_history`, `competition_history` | Append-only observations |
| `opportunities`, `opportunity_events` | Opportunities and their lifecycle |
| `profitability_snapshots`, `risk_assessments` | Frozen economics and risk |
| `opportunity_validations` | Personal testing mode |
| `purchases`, `sales`, `outcomes` | Realised inventory |
| `capital_plans` | Saved allocation runs |
| `provider_requests`, `provider_health` | Provider reliability and cost |
