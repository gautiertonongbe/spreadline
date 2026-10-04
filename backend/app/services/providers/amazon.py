"""Amazon adapter.

The interface, the capability declaration and the payload mapper are real and
tested. The transport is not wired to a live endpoint, because this repository
has no Amazon credentials and inventing a fake integration would be worse than
having none: it would produce numbers that look like market data.

What exists here:

* ``map_listing`` / ``map_offer`` / ``map_price_point`` - pure functions from a
  provider payload to Spreadline DTOs, unit tested against sample payloads.
* capability declaration, so the platform knows what to expect once it is live.
* a transport hook that raises ``ProviderNotConfiguredError`` until credentials
  and a base URL are supplied.

To make it live: implement ``_request`` against the chosen data source, set the
credentials in the environment, and add the slug to ``ENABLED_PROVIDERS``.
Nothing above this file changes.

The options, as of September 2026:

* **SP-API**, which needs a Professional seller account and app registration.
* **Creators API**, which replaced the Product Advertising API. It needs an
  Amazon Associates account with at least 10 qualifying sales in the trailing
  30 days, and offers SearchItems, GetItems, GetVariations and GetBrowseNodes.
* a **licensed aggregator** such as Keepa or Rainforest, which needs neither
  account but costs per request or per month.

Note that **PA-API v5 is retired**: Amazon stopped accepting new customers and
calls to it now return HTTP 403. Any guide that tells you to sign up for it is
out of date.

Scraping is not an option here. Beyond the terms-of-service problem, the public
scrapers all rely on fingerprint impersonation to evade anti-bot measures, which
is exactly the category of behaviour this platform refuses to implement.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.core.clock import ensure_utc, utcnow
from app.core.config import settings
from app.core.errors import ProviderNotConfiguredError
from app.core.money import money
from app.core.security import provider_credentials
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawIdentifier,
    RawListing,
    RawOffer,
    RawPricePoint,
)

#: Amazon availability strings seen in the wild, mapped to the platform's view.
_AVAILABILITY_MAP = {
    "in stock": Availability.IN_STOCK,
    "instock": Availability.IN_STOCK,
    "available": Availability.IN_STOCK,
    "only a few left": Availability.LIMITED,
    "limited": Availability.LIMITED,
    "out of stock": Availability.OUT_OF_STOCK,
    "unavailable": Availability.OUT_OF_STOCK,
    "preorder": Availability.PREORDER,
    "pre-order": Availability.PREORDER,
}

_CONDITION_MAP = {
    "new": Condition.NEW,
    "renewed": Condition.RENEWED,
    "refurbished": Condition.RENEWED,
    "used_like_new": Condition.USED_LIKE_NEW,
    "used - like new": Condition.USED_LIKE_NEW,
    "used_very_good": Condition.USED_GOOD,
    "used_good": Condition.USED_GOOD,
    "used - good": Condition.USED_GOOD,
    "used_acceptable": Condition.USED_ACCEPTABLE,
    "used - acceptable": Condition.USED_ACCEPTABLE,
}


def parse_availability(value: Any) -> Availability:
    if value is None:
        return Availability.UNKNOWN
    if isinstance(value, bool):
        return Availability.IN_STOCK if value else Availability.OUT_OF_STOCK
    return _AVAILABILITY_MAP.get(str(value).strip().lower(), Availability.UNKNOWN)


def parse_condition(value: Any) -> Condition:
    if value is None:
        return Condition.NEW
    return _CONDITION_MAP.get(str(value).strip().lower(), Condition.UNKNOWN)


def parse_money(value: Any) -> Decimal | None:
    """Amounts arrive as numbers, strings, or {amount, currency} objects.

    Returns None for a missing amount rather than zero: an absent price is not a
    price of nothing.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return parse_money(value.get("amount", value.get("Amount", value.get("value"))))
    try:
        return money(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, ArithmeticError):
        return None


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return utcnow()


def map_listing(payload: dict[str, Any], *, provider_slug: str = "amazon") -> RawListing:
    """Map one Amazon item payload to a ``RawListing``.

    Tolerant of field-name variation between SP-API, PA-API and aggregator
    shapes, because the platform should not break when a provider renames a key.
    """
    attributes = payload.get("attributes") or payload.get("Attributes") or {}
    summaries = payload.get("summaries") or []
    summary = summaries[0] if summaries else {}

    asin = str(
        payload.get("asin") or payload.get("ASIN") or payload.get("external_id") or ""
    ).strip()
    title = str(
        payload.get("title") or summary.get("itemName") or payload.get("Title") or ""
    ).strip()

    identifiers: list[RawIdentifier] = []
    if asin:
        identifiers.append(RawIdentifier(identifier_type=IdentifierType.ASIN.value, value=asin))
    for key, kind in (
        ("upc", IdentifierType.UPC),
        ("ean", IdentifierType.EAN),
        ("gtin", IdentifierType.GTIN),
        ("isbn", IdentifierType.ISBN),
        ("part_number", IdentifierType.MPN),
        ("mpn", IdentifierType.MPN),
        ("model_number", IdentifierType.MODEL),
    ):
        value = payload.get(key) or attributes.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("value")
        if value:
            identifiers.append(RawIdentifier(identifier_type=kind.value, value=str(value)))

    sales_ranks = payload.get("salesRanks") or payload.get("sales_ranks") or []
    rank, rank_category = None, None
    if isinstance(sales_ranks, list) and sales_ranks:
        first = sales_ranks[0]
        if isinstance(first, dict):
            rank = first.get("rank") or first.get("value")
            rank_category = first.get("title") or first.get("category")
    rank = rank if isinstance(rank, int) else payload.get("sales_rank")

    return RawListing(
        marketplace=Marketplace.AMAZON,
        external_id=asin,
        title=title,
        brand=payload.get("brand") or summary.get("brand") or attributes.get("brand"),
        manufacturer=payload.get("manufacturer") or summary.get("manufacturer"),
        model=payload.get("model") or attributes.get("model_number"),
        category=payload.get("category")
        or summary.get("browseClassification", {}).get("displayName"),
        sku=payload.get("sku"),
        url=payload.get("url") or (f"https://www.amazon.com/dp/{asin}" if asin else None),
        image_url=payload.get("image_url") or payload.get("mainImage", {}).get("link"),
        condition=parse_condition(payload.get("condition")),
        identifiers=tuple(identifiers),
        attributes={str(k): v for k, v in attributes.items()}
        if isinstance(attributes, dict)
        else {},
        price=parse_money(payload.get("price") or payload.get("listing_price")),
        shipping=parse_money(payload.get("shipping")),
        availability=parse_availability(payload.get("availability")),
        seller_count=payload.get("seller_count") or payload.get("number_of_sellers"),
        offer_count=payload.get("offer_count") or payload.get("total_offer_count"),
        sales_rank=rank,
        rank_category=rank_category,
        review_count=payload.get("review_count") or payload.get("total_reviews"),
        rating=(
            Decimal(str(payload["rating"])) if payload.get("rating") not in (None, "") else None
        ),
        quantity_available=payload.get("quantity") or payload.get("quantity_available"),
        observed_at=parse_timestamp(payload.get("observed_at")),
        provider=provider_slug,
    )


def map_offer(payload: dict[str, Any]) -> RawOffer:
    price = parse_money(payload.get("price") or payload.get("ListingPrice")) or Decimal("0")
    shipping = parse_money(payload.get("shipping") or payload.get("Shipping")) or Decimal("0")
    fulfillment = payload.get("fulfillment") or payload.get("fulfillmentChannel")
    return RawOffer(
        external_offer_id=payload.get("offer_id") or payload.get("OfferId"),
        seller_id=payload.get("seller_id") or payload.get("SellerId"),
        seller_name=payload.get("seller_name") or payload.get("SellerName"),
        price=money(price),
        shipping=money(shipping),
        condition=parse_condition(payload.get("condition") or payload.get("SubCondition")),
        fulfillment=("fba" if str(fulfillment).lower() in {"amazon", "afn", "fba"} else "fbm")
        if fulfillment
        else None,
        is_buy_box=bool(payload.get("is_buy_box") or payload.get("IsBuyBoxWinner")),
        is_marketplace_seller=bool(
            payload.get("is_amazon") or payload.get("IsFulfilledByAmazon") is True
        )
        and str(payload.get("seller_name", "")).lower() == "amazon",
        availability=parse_availability(payload.get("availability")),
        quantity_available=payload.get("quantity"),
        observed_at=parse_timestamp(payload.get("observed_at")),
    )


def map_price_point(payload: dict[str, Any]) -> RawPricePoint:
    return RawPricePoint(
        price=money(parse_money(payload.get("price")) or Decimal("0")),
        shipping=money(parse_money(payload.get("shipping")) or Decimal("0")),
        availability=parse_availability(payload.get("availability")),
        condition=parse_condition(payload.get("condition")),
        is_buy_box=bool(payload.get("is_buy_box", True)),
        seller_id=payload.get("seller_id"),
        seller_name=payload.get("seller_name"),
        observed_at=parse_timestamp(payload.get("observed_at") or payload.get("date")),
        currency=payload.get("currency", "USD"),
    )


class AmazonProvider(MarketplaceProvider):
    slug = "amazon"
    marketplace = Marketplace.AMAZON
    display_name = "Amazon"
    is_live = True
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
            ProviderCapability.DEMAND,
        }
    )

    def __init__(self, *, base_url: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = base_url
        self._client = client
        self._credentials = provider_credentials("amazon")

    @property
    def is_configured(self) -> bool:
        return bool(self._credentials and self.base_url)

    @property
    def configuration_note(self) -> str | None:
        if self.is_configured:
            return None
        missing = []
        if not self._credentials:
            missing.append("AMAZON_PROVIDER_CREDENTIALS")
        if not self.base_url:
            missing.append("a provider base URL")
        return (
            "Not configured: set " + " and ".join(missing) + ". "
            "The adapter and payload mapper are implemented; only the transport is unwired."
        )

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"Amazon provider is not configured. {self.configuration_note}",
                provider=self.slug,
            )
        client = self._client or httpx.AsyncClient(
            base_url=self.base_url or "", timeout=settings.provider_timeout_seconds
        )
        response = await client.get(
            path, params=params, headers={"Authorization": f"Bearer {self._credentials}"}
        )
        response.raise_for_status()
        return response.json()

    async def get_product(self, external_id: str) -> RawListing:
        payload = await self._request(f"/catalog/items/{external_id}")
        return map_listing(payload, provider_slug=self.slug)

    async def get_price(self, external_id: str) -> RawPricePoint:
        payload = await self._request(f"/pricing/{external_id}")
        return map_price_point(payload)

    async def get_offers(self, external_id: str) -> tuple[RawOffer, ...]:
        payload = await self._request(f"/pricing/{external_id}/offers")
        return tuple(map_offer(offer) for offer in payload.get("offers", []))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
