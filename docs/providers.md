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

## The platform catalogue

`services/providers/platforms.py` describes every data source Spreadline knows
about, connected or not: what it is, what it could answer, what credential it
needs, what it costs and whether it requires a seller account. It is served at
`GET /api/v1/platforms` and rendered on the Providers screen, so "what can we
connect and what would each take" is answered by the running system rather than
by a roadmap document that drifts from the code.

Three states, and the distinction is deliberate:

| State | Meaning |
| --- | --- |
| `live` | Implemented and wired to a real endpoint. Set the credential and it works. |
| `adapter_ready` | Interface, capabilities and payload mapper implemented and tested; transport unwired. Connecting it is finishing one method. |
| `planned` | Described, registered, and refuses every call with the reason. |

A planned platform **never returns an empty result**. A stub returning `[]` would
be indistinguishable from a real provider reporting genuinely empty results, and
the difference matters everywhere: no offers means no competition data, which
lowers confidence and changes a decision. Silence has to be loud.

### Live today, free, no seller account

| Slug | Marketplace | Role | Capabilities |
| --- | --- | --- | --- |
| `bestbuy` | Best Buy | Source | search, product, price, inventory |
| `ebay` | eBay | Source and exit | search, product, price, offers, inventory, competition |

`ENABLED_PROVIDERS=free` registers the pair.

**Best Buy** is unusually well suited to this platform: it returns `upc`, so the
identity engine works on real identifiers rather than title similarity, and
`shippingWeight`, which the fee engine needs and most catalogue APIs omit. It is
a single retailer, so it declares no offers or competition capability and reports
`seller_count = None` rather than inventing a market structure that does not
exist there.

**eBay** supports lookup by GTIN, which is the whole ladder working end to end: a
product identified at a retailer by UPC is found on the marketplace by the same
identifier. A GTIN search also returns every concurrent listing for that product,
which is a genuine offer set, so seller count, price dispersion and the lowest
offer are measured rather than assumed. eBay has no buy box, so the cheapest
landed offer is marked instead, and that decision lives in `get_offers` rather
than being asserted per item.

Its application token lives about two hours and is cached in memory behind a
lock, because exchanging a token per request would spend a meaningful share of a
5,000 call daily allowance on authentication.

### Adapter ready

| Slug | Blocker |
| --- | --- |
| `amazon` | SP-API needs LWA token exchange and request signing, plus a Professional seller account |
| `walmart` | Marketplace API needs a seller account and a signed OAuth2 flow |

Payload mappers are implemented and unit tested against sample payloads. Only the
transport is unwired, and deliberately so: a fabricated live integration produces
numbers that look like market data and would be acted on.

### Planned

Data feeds: `keepa`, `rainforest`, `bluecart`, `serpapi`. Keepa is the highest
value addition by some margin, because it supplies years of Amazon price and
rank history, the one input the statistics and anomaly engines cannot build
quickly on their own.

Marketplaces and retailers: `shopify`, `etsy`, `target`, `homedepot`.

Wholesale and distributor: `faire`, `alibaba`. Both are flagged with the same
caveat: minimum order quantity, lead time, duty and freight are not yet inputs to
the profitability engine, so their economics would be incomplete until they are.

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
