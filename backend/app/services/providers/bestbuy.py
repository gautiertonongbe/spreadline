"""Best Buy Products API.

A fully wired live provider. Unlike the Amazon and Walmart adapters, this one
calls a real endpoint, because Best Buy issues a free developer key with no
seller account and no per-request cost, which makes it the shortest path from
this repository to real market data.

Why it is a good source for this platform specifically:

* it returns ``upc``, so the identity engine works on real identifiers rather
  than on title similarity,
* it returns ``shippingWeight``, which the fee engine needs and which most
  catalogue APIs omit,
* prices are near real time.

What it is not: a marketplace. Best Buy is a single retailer, so there is no
seller count, no buy box and no offer set. The provider declares only the
capabilities it actually has, and the competition engine is told nothing rather
than being handed a fabricated seller count of one.

Licensing: a free key covers development and testing. Commercial use requires a
partner agreement with Best Buy. That is the operator's to arrange, and the
configuration note says so rather than leaving it to be discovered later.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import NotFoundError, ProviderError, ProviderNotConfiguredError
from app.core.money import money
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawIdentifier,
    RawInventory,
    RawListing,
    RawPricePoint,
    RawSearchResult,
)

#: Fields requested explicitly. The API returns a trimmed object by default, and
#: asking for exactly what is mapped keeps the payload small and the mapping
#: total.
SHOW_FIELDS = ",".join(
    [
        "sku",
        "name",
        "upc",
        "manufacturer",
        "modelNumber",
        "salePrice",
        "regularPrice",
        "onSale",
        "onlineAvailability",
        "inStoreAvailability",
        "orderable",
        "customerReviewAverage",
        "customerReviewCount",
        "url",
        "image",
        "categoryPath",
        "shippingWeight",
        "shippingCost",
        "condition",
        "salesRankShortTerm",
    ]
)


def _decimal(value: Any) -> Decimal | None:
    """Best Buy sends prices as JSON numbers; they become Decimal via str."""
    if value is None or value == "":
        return None
    try:
        return money(str(value))
    except (ValueError, ArithmeticError):
        return None


def _availability(payload: dict[str, Any]) -> Availability:
    """Map Best Buy's several availability flags to one state.

    ``orderable`` is the authoritative field when present; the boolean
    availability flags are the fallback. An unknown state stays unknown rather
    than defaulting to in stock, because a wrong "in stock" turns into a
    purchase order for something that cannot be bought.
    """
    orderable = payload.get("orderable")
    if isinstance(orderable, str):
        normalized = orderable.strip().lower()
        if normalized == "available":
            return Availability.IN_STOCK
        if normalized in {"comingsoon", "preorder"}:
            return Availability.PREORDER
        if normalized in {"soldout", "discontinued", "unavailable", "backorder"}:
            return Availability.OUT_OF_STOCK
    online = payload.get("onlineAvailability")
    if online is True:
        return Availability.IN_STOCK
    if online is False:
        return (
            Availability.LIMITED
            if payload.get("inStoreAvailability") is True
            else Availability.OUT_OF_STOCK
        )
    return Availability.UNKNOWN


def _category(payload: dict[str, Any]) -> str | None:
    """The leaf of the category path, which is what the fee tables key on."""
    path = payload.get("categoryPath")
    if isinstance(path, list) and path:
        leaf = path[-1]
        if isinstance(leaf, dict):
            return leaf.get("name")
        if isinstance(leaf, str):
            return leaf
    return None


def map_listing(payload: dict[str, Any], *, provider_slug: str = "bestbuy") -> RawListing:
    """Map one Best Buy product to a ``RawListing``."""
    sku = str(payload.get("sku") or "").strip()

    identifiers: list[RawIdentifier] = []
    if sku:
        identifiers.append(
            RawIdentifier(identifier_type=IdentifierType.BESTBUY_SKU.value, value=sku)
        )
    if payload.get("upc"):
        identifiers.append(
            RawIdentifier(identifier_type=IdentifierType.UPC.value, value=str(payload["upc"]))
        )
    if payload.get("modelNumber"):
        identifiers.append(
            RawIdentifier(
                identifier_type=IdentifierType.MPN.value, value=str(payload["modelNumber"])
            )
        )

    attributes: dict[str, Any] = {}
    if payload.get("shippingWeight") is not None:
        # The fee engine reads weight_lb; Best Buy reports pounds.
        attributes["weight_lb"] = str(payload["shippingWeight"])
    if payload.get("onSale") is not None:
        attributes["on_sale"] = str(payload["onSale"])
    if payload.get("regularPrice") is not None:
        attributes["regular_price"] = str(payload["regularPrice"])

    return RawListing(
        marketplace=Marketplace.BESTBUY,
        external_id=sku,
        title=str(payload.get("name") or "").strip(),
        brand=payload.get("manufacturer"),
        manufacturer=payload.get("manufacturer"),
        model=payload.get("modelNumber"),
        category=_category(payload),
        sku=sku or None,
        url=payload.get("url"),
        image_url=payload.get("image"),
        condition=Condition.NEW,
        identifiers=tuple(identifiers),
        attributes=attributes,
        price=_decimal(payload.get("salePrice")),
        shipping=_decimal(payload.get("shippingCost")),
        availability=_availability(payload),
        # A single retailer has no seller count and no offer set. Reporting one
        # would be inventing a market structure that does not exist here.
        seller_count=None,
        offer_count=None,
        sales_rank=payload.get("salesRankShortTerm"),
        rank_category=_category(payload),
        review_count=payload.get("customerReviewCount"),
        rating=(
            Decimal(str(payload["customerReviewAverage"]))
            if payload.get("customerReviewAverage") not in (None, "")
            else None
        ),
        quantity_available=None,
        observed_at=utcnow(),
        provider=provider_slug,
    )


class BestBuyProvider(MarketplaceProvider):
    slug = "bestbuy"
    marketplace = Marketplace.BESTBUY
    display_name = "Best Buy"
    is_live = True
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.INVENTORY,
        }
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key if api_key is not None else settings.bestbuy_api_key
        self.base_url = base_url or settings.bestbuy_base_url
        self._client = client
        self._owns_client = client is None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def configuration_note(self) -> str | None:
        if self.is_configured:
            return (
                "Live. A free developer key covers development and testing; "
                "commercial use requires a partner agreement with Best Buy."
            )
        return (
            "Not configured: set BESTBUY_API_KEY. A free key is issued instantly at "
            "developer.bestbuy.com with no seller account and no per-request cost."
        )

    async def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=settings.provider_timeout_seconds
            )
        return self._client

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"Best Buy provider is not configured. {self.configuration_note}",
                provider=self.slug,
            )
        client = await self._client_or_new()
        query = {
            "apiKey": self._api_key,
            "format": "json",
            **(params or {}),
        }
        response = await client.get(path, params=query)
        if response.status_code == 403:
            raise ProviderNotConfiguredError(
                "Best Buy rejected the API key (403). Check BESTBUY_API_KEY is "
                "active and not rate limited.",
                provider=self.slug,
            )
        if response.status_code >= 400:
            # The body can echo the query, which contains the key, so only the
            # status reaches the caller and the logs.
            raise ProviderError(
                f"Best Buy returned HTTP {response.status_code}.",
                provider=self.slug,
                status_code=response.status_code,
            )
        return response.json()

    async def search_products(self, query: str, *, limit: int = 20) -> RawSearchResult:
        # The search grammar is (attribute=value); a keyword search matches the
        # product name, and identifiers are routed to an exact lookup instead.
        escaped = query.replace("&", " ").replace("(", " ").replace(")", " ").strip()
        payload = await self._request(
            f"/products(search={escaped})",
            {"show": SHOW_FIELDS, "pageSize": min(limit, 100), "page": 1},
        )
        products = payload.get("products") or []
        return RawSearchResult(
            listings=tuple(map_listing(item, provider_slug=self.slug) for item in products),
            total_results=payload.get("total"),
            query=query,
            truncated=bool(payload.get("totalPages", 1) > 1),
        )

    async def get_product(self, external_id: str) -> RawListing:
        payload = await self._request(f"/products/{external_id}.json", {"show": SHOW_FIELDS})
        # A single-product lookup returns the object itself, not a collection.
        if "sku" not in payload:
            products = payload.get("products") or []
            if not products:
                raise NotFoundError(
                    f"No Best Buy product with SKU {external_id}.", provider=self.slug
                )
            payload = products[0]
        return map_listing(payload, provider_slug=self.slug)

    async def get_product_by_identifier(
        self, identifier_type: str, value: str
    ) -> RawListing | None:
        field = {
            IdentifierType.UPC.value: "upc",
            IdentifierType.GTIN.value: "upc",
            IdentifierType.EAN.value: "upc",
            IdentifierType.MPN.value: "modelNumber",
            IdentifierType.MODEL.value: "modelNumber",
            IdentifierType.BESTBUY_SKU.value: "sku",
        }.get(identifier_type)
        if field is None:
            return None
        payload = await self._request(
            f"/products({field}={value})", {"show": SHOW_FIELDS, "pageSize": 1}
        )
        products = payload.get("products") or []
        return map_listing(products[0], provider_slug=self.slug) if products else None

    async def get_price(self, external_id: str) -> RawPricePoint:
        listing = await self.get_product(external_id)
        return RawPricePoint(
            price=money(listing.price or Decimal("0")),
            shipping=money(listing.shipping or Decimal("0")),
            availability=listing.availability,
            condition=listing.condition,
            is_buy_box=True,
            seller_id="bestbuy",
            seller_name="Best Buy",
            observed_at=listing.observed_at,
        )

    async def get_inventory(self, external_id: str) -> RawInventory:
        listing = await self.get_product(external_id)
        return RawInventory(
            availability=listing.availability,
            # Best Buy does not publish unit counts. None means unknown, and the
            # capital allocator treats unknown supply as a constraint rather
            # than as an unlimited one.
            quantity_available=None,
            max_order_quantity=None,
            observed_at=listing.observed_at,
        )

    async def health_check(self) -> bool:
        if not self.is_configured:
            return False
        try:
            await self._request("/products(sku=6084400)", {"show": "sku", "pageSize": 1})
            return True
        except Exception:  # noqa: BLE001 - a health check must not raise
            return False

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
