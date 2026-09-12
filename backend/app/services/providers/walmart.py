"""Walmart adapter.

Same posture as the Amazon adapter: interface, capabilities and payload mapper
are real and tested; the transport raises until credentials exist. See
``amazon.py`` for the reasoning.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderNotConfiguredError
from app.core.money import money
from app.core.security import provider_credentials
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.amazon import parse_money, parse_timestamp
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawIdentifier,
    RawListing,
    RawOffer,
    RawPricePoint,
)

_STOCK_MAP = {
    "available": Availability.IN_STOCK,
    "in_stock": Availability.IN_STOCK,
    "instock": Availability.IN_STOCK,
    "limited_stock": Availability.LIMITED,
    "limited": Availability.LIMITED,
    "out_of_stock": Availability.OUT_OF_STOCK,
    "unavailable": Availability.OUT_OF_STOCK,
    "preorder": Availability.PREORDER,
}


def parse_stock(value: Any) -> Availability:
    if value is None:
        return Availability.UNKNOWN
    if isinstance(value, bool):
        return Availability.IN_STOCK if value else Availability.OUT_OF_STOCK
    return _STOCK_MAP.get(str(value).strip().lower().replace(" ", "_"), Availability.UNKNOWN)


def map_listing(payload: dict[str, Any], *, provider_slug: str = "walmart") -> RawListing:
    item_id = str(
        payload.get("itemId") or payload.get("item_id") or payload.get("usItemId") or ""
    ).strip()
    identifiers: list[RawIdentifier] = []
    if item_id:
        identifiers.append(
            RawIdentifier(identifier_type=IdentifierType.WALMART_ITEM_ID.value, value=item_id)
        )
    for key, kind in (
        ("upc", IdentifierType.UPC),
        ("gtin", IdentifierType.GTIN),
        ("ean", IdentifierType.EAN),
        ("modelNumber", IdentifierType.MODEL),
        ("model_number", IdentifierType.MODEL),
        ("manufacturerPartNumber", IdentifierType.MPN),
    ):
        value = payload.get(key)
        if value:
            identifiers.append(RawIdentifier(identifier_type=kind.value, value=str(value)))

    price = parse_money(
        payload.get("salePrice") if payload.get("salePrice") is not None else payload.get("price")
    )
    attributes = payload.get("attributes") or {}

    return RawListing(
        marketplace=Marketplace.WALMART,
        external_id=item_id,
        title=str(payload.get("name") or payload.get("title") or "").strip(),
        brand=payload.get("brandName") or payload.get("brand"),
        manufacturer=payload.get("manufacturer"),
        model=payload.get("modelNumber") or payload.get("model"),
        category=payload.get("categoryPath") or payload.get("category"),
        sku=payload.get("sku"),
        url=payload.get("productUrl")
        or payload.get("url")
        or (f"https://www.walmart.com/ip/{item_id}" if item_id else None),
        image_url=payload.get("largeImage") or payload.get("thumbnailImage"),
        condition=Condition.NEW if not payload.get("condition") else Condition.UNKNOWN,
        identifiers=tuple(identifiers),
        attributes={str(k): v for k, v in attributes.items()}
        if isinstance(attributes, dict)
        else {},
        price=price,
        shipping=parse_money(payload.get("standardShipRate")),
        availability=parse_stock(payload.get("stock") or payload.get("availabilityStatus")),
        seller_count=payload.get("numberOfSellers") or payload.get("seller_count"),
        offer_count=payload.get("offer_count"),
        sales_rank=payload.get("salesRank") or payload.get("sales_rank"),
        rank_category=payload.get("rank_category"),
        review_count=payload.get("numReviews") or payload.get("review_count"),
        rating=(
            Decimal(str(payload["customerRating"]))
            if payload.get("customerRating") not in (None, "")
            else None
        ),
        quantity_available=payload.get("quantity") or payload.get("availableQuantity"),
        observed_at=parse_timestamp(payload.get("observed_at")),
        provider=provider_slug,
    )


def map_offer(payload: dict[str, Any]) -> RawOffer:
    return RawOffer(
        external_offer_id=payload.get("offerId") or payload.get("offer_id"),
        seller_id=payload.get("sellerId") or payload.get("seller_id"),
        seller_name=payload.get("sellerName") or payload.get("seller_name"),
        price=money(parse_money(payload.get("price")) or Decimal("0")),
        shipping=money(parse_money(payload.get("shippingPrice")) or Decimal("0")),
        condition=Condition.NEW,
        fulfillment="wfs"
        if payload.get("wfsEligible") or payload.get("fulfilledByWalmart")
        else "seller",
        is_buy_box=bool(payload.get("isBuyBoxWinner") or payload.get("is_buy_box")),
        is_marketplace_seller=str(payload.get("sellerName", "")).strip().lower() == "walmart.com",
        availability=parse_stock(payload.get("availability")),
        quantity_available=payload.get("quantity"),
        observed_at=parse_timestamp(payload.get("observed_at")),
    )


def map_price_point(payload: dict[str, Any]) -> RawPricePoint:
    return RawPricePoint(
        price=money(parse_money(payload.get("price") or payload.get("salePrice")) or Decimal("0")),
        shipping=money(parse_money(payload.get("shippingPrice")) or Decimal("0")),
        availability=parse_stock(payload.get("stock")),
        condition=Condition.NEW,
        is_buy_box=bool(payload.get("is_buy_box", True)),
        seller_id=payload.get("sellerId"),
        seller_name=payload.get("sellerName"),
        observed_at=parse_timestamp(payload.get("observed_at") or payload.get("date")),
        currency=payload.get("currency", "USD"),
    )


class WalmartProvider(MarketplaceProvider):
    slug = "walmart"
    marketplace = Marketplace.WALMART
    display_name = "Walmart"
    is_live = True
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
        }
    )

    def __init__(self, *, base_url: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = base_url
        self._client = client
        self._credentials = provider_credentials("walmart")

    @property
    def is_configured(self) -> bool:
        return bool(self._credentials and self.base_url)

    @property
    def configuration_note(self) -> str | None:
        if self.is_configured:
            return None
        missing = []
        if not self._credentials:
            missing.append("WALMART_PROVIDER_CREDENTIALS")
        if not self.base_url:
            missing.append("a provider base URL")
        return (
            "Not configured: set " + " and ".join(missing) + ". "
            "The adapter and payload mapper are implemented; only the transport is unwired."
        )

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"Walmart provider is not configured. {self.configuration_note}",
                provider=self.slug,
            )
        client = self._client or httpx.AsyncClient(
            base_url=self.base_url or "", timeout=settings.provider_timeout_seconds
        )
        response = await client.get(
            path, params=params, headers={"WM_SEC.ACCESS_TOKEN": self._credentials or ""}
        )
        response.raise_for_status()
        return response.json()

    async def get_product(self, external_id: str) -> RawListing:
        payload = await self._request(f"/items/{external_id}")
        return map_listing(payload, provider_slug=self.slug)

    async def get_price(self, external_id: str) -> RawPricePoint:
        payload = await self._request(f"/items/{external_id}/price")
        return map_price_point(payload)

    async def get_offers(self, external_id: str) -> tuple[RawOffer, ...]:
        payload = await self._request(f"/items/{external_id}/offers")
        return tuple(map_offer(offer) for offer in payload.get("offers", []))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
