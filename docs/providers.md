# Providers

Spreadline never depends on one data source. Providers change, get repriced,
cover different fields and disappear, so everything above this layer is written
against one interface.

## The interface

```python
class MarketplaceProvider:
    slug: str
    marketplace: Marketplace
    capabilities: frozenset[ProviderCapability]
    is_live: bool

    async def search_products(query, *, limit) -> RawSearchResult
    async def get_product(external_id) -> RawListing
    async def get_product_by_identifier(identifier_type, value) -> RawListing | None
    async def get_price(external_id) -> RawPricePoint
    async def get_offers(external_id) -> tuple[RawOffer, ...]
    async def get_inventory(external_id) -> RawInventory
    async def get_history(external_id, *, days) -> tuple[RawPricePoint, ...]
    async def get_demand(external_id, *, days) -> tuple[RawDemandPoint, ...]
    async def get_competition(external_id, *, days) -> tuple[RawCompetitionPoint, ...]
```

Capabilities are **declared**, and the default implementation of every method
raises `ProviderCapabilityError`. A provider that cannot answer raises; it never
returns an empty result. "No demand data" and "demand of zero" are different
facts and the distinction has to survive the boundary, because one of them
changes a decision and the other changes a score.

A provider without credentials raises `ProviderNotConfiguredError` and never
silently falls back to fixture data.

## Registered providers

| Slug | Marketplace | Live | Capabilities |
| --- | --- | --- | --- |
| `mock_amazon` | amazon | no | all eight |
| `mock_walmart` | walmart | no | all eight |
| `amazon` | amazon | yes | search, product, price, offers, inventory, demand |
| `walmart` | walmart | yes | search, product, price, offers, inventory |

`ENABLED_PROVIDERS=mock` expands to both fixture providers, since one marketplace
alone cannot produce a cross-market opportunity.

The registry prefers a **live, configured** provider over a fixture. Once
credentials exist the platform uses them with no code change. An unconfigured
provider is still registered and appears in `/providers` with the reason it
cannot be used, rather than vanishing and leaving the operator guessing.

## The live adapters

`amazon.py` and `walmart.py` ship with:

- a full capability declaration,
- payload mappers (`map_listing`, `map_offer`, `map_price_point`) that are pure,
  tolerant of field-name variation between API shapes, and unit tested against
  sample payloads,
- a transport hook that raises until credentials and a base URL are supplied.

They are deliberately not wired to a live endpoint. This repository has no
credentials, and a fabricated integration would be worse than an absent one: it
would produce numbers that look like market data and would be acted on.

To make one live: implement `_request` against the chosen source (SP-API, a
licensed aggregator, whatever the commercial arrangement supports), set the
credentials in the environment, and add the slug to `ENABLED_PROVIDERS`.

## Reliability

Every provider is wrapped in `ReliableProvider`, which presents the same
interface so callers cannot tell the difference and cannot bypass it.

- **Timeout** per call.
- **Retries** with exponential backoff and **full jitter**. Synchronised retries
  from several workers are how a degraded provider becomes a dead one.
- **Only transport-shaped failures are retried.** A capability error, a missing
  credential or a not-found is a permanent answer; retrying it three times burns
  quota to arrive at the same place.
- **Circuit breaker.** Opens after consecutive failures, allows one probe after
  the reset window, reopens for another full window if the probe fails.
- **Token-bucket rate limiter**, smoothing bursts rather than rejecting them.
- **Health and metrics**: request count, success rate, mean and p95 latency,
  consecutive failures, last error, circuit state. Visible at `/providers/health`.

## Telemetry

Provider calls are buffered in memory and flushed after the request completes.
Writing them inside the request would open a second database session against the
transaction still in flight; on SQLite that blocks until the lock times out, and
on any database it is an unnecessary round trip on the hot path. The buffer is
bounded, so telemetry can never grow without limit.

## Freshness

TTLs are configured per data type: current price and availability are short,
competition medium, product metadata long. Data older than its TTL is not
silently trusted; the data-quality dimension is downgraded and the staleness is
reported. Refreshes are bounded batches over listings behind live opportunities,
not a crawl of the catalogue.

## Compliance

Spreadline uses official APIs, licensed providers and permitted integrations. It
does not automate retailer checkout, purchasing, payment or fulfilment, does not
bypass CAPTCHAs, and does not scrape without authorisation. The provider
abstraction exists partly so that changing what is permitted changes an adapter
rather than the product.
