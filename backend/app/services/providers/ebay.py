"""eBay Browse API.

A fully wired live provider. A free eBay developer account grants an application
token through the OAuth2 client-credentials flow, with a default allowance of
5,000 calls per day and no per-request cost, which makes it usable for real work
without a seller account or a subscription.

It is the most useful free *marketplace* source available to this platform, for
two reasons:

* ``item_summary/search`` accepts a ``gtin`` parameter, so a product found on a
  retailer by UPC can be looked up on eBay by the same identifier. That is the
  identity ladder working on real data rather than on title similarity.
* a search returns many concurrent listings for one product, which is a genuine
  offer set: seller count, price dispersion and the lowest offer are measured
  rather than assumed.

Token handling: the application token lives about two hours. It is cached in
memory and refreshed slightly before expiry, because spending a call on a token
refresh per request would burn a meaningful share of a 5,000 call allowance.
"""

from __future__ import annotations

import asyncio
import base64
import statistics
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.core.clock import ensure_utc, utcnow
from app.core.config import settings
from app.core.errors import NotFoundError, ProviderError, ProviderNotConfiguredError
from app.core.money import money
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawCompetitionPoint,
    RawIdentifier,
    RawInventory,
    RawListing,
    RawOffer,
    RawPricePoint,
    RawSearchResult,
)

PRODUCTION_HOST = "https://api.ebay.com"
SANDBOX_HOST = "https://api.sandbox.ebay.com"
#: The only scope the Browse API needs for public item data.
SCOPE = "https://api.ebay.com/oauth/api_scope"
#: Refresh this long before the token actually expires.
TOKEN_SAFETY_MARGIN = timedelta(minutes=5)

#: eBay condition vocabulary mapped to the platform's. The distinction matters:
#: the variation engine blocks a match between a new unit and a used one.
_CONDITION_MAP = {
    "NEW": Condition.NEW,
    "LIKE_NEW": Condition.USED_LIKE_NEW,
    "NEW_OTHER": Condition.NEW,
    "NEW_WITH_DEFECTS": Condition.NEW,
    "CERTIFIED_REFURBISHED": Condition.RENEWED,
    "EXCELLENT_REFURBISHED": Condition.RENEWED,
    "VERY_GOOD_REFURBISHED": Condition.RENEWED,
    "GOOD_REFURBISHED": Condition.RENEWED,
    "SELLER_REFURBISHED": Condition.RENEWED,
    "USED_EXCELLENT": Condition.USED_LIKE_NEW,
    "USED_VERY_GOOD": Condition.USED_GOOD,
    "USED_GOOD": Condition.USED_GOOD,
    "USED_ACCEPTABLE": Condition.USED_ACCEPTABLE,
    "FOR_PARTS_OR_NOT_WORKING": Condition.USED_ACCEPTABLE,
}


def parse_condition(value: Any) -> Condition:
    if not value:
        return Condition.UNKNOWN
    return _CONDITION_MAP.get(str(value).strip().upper().replace(" ", "_"), Condition.UNKNOWN)


def parse_amount(container: Any) -> Decimal | None:
    """eBay money is ``{"value": "12.34", "currency": "USD"}``."""
    if not isinstance(container, dict):
        return None
    value = container.get("value")
    if value in (None, ""):
        return None
    try:
        return money(str(value))
    except (ValueError, ArithmeticError):
        return None


def parse_shipping(payload: dict[str, Any]) -> Decimal:
    """Lowest shipping cost across the offered options.

    Free shipping is a real zero; an absent shippingOptions block means the cost
    is unknown, and unknown is returned as zero only because the caller treats
    the landed price as a lower bound. The distinction is recorded in the
    attributes so it is not silently lost.
    """
    options = payload.get("shippingOptions")
    if not isinstance(options, list) or not options:
        return Decimal("0")
    costs = [
        amount
        for amount in (parse_amount(option.get("shippingCost")) for option in options)
        if amount is not None
    ]
    return min(costs) if costs else Decimal("0")


def map_listing(payload: dict[str, Any], *, provider_slug: str = "ebay") -> RawListing:
    """Map one eBay item summary (or item) to a ``RawListing``."""
    item_id = str(payload.get("itemId") or payload.get("legacyItemId") or "").strip()

    identifiers: list[RawIdentifier] = []
    if item_id:
        identifiers.append(
            RawIdentifier(identifier_type=IdentifierType.EBAY_ITEM_ID.value, value=item_id)
        )
    for key, kind in (
        ("gtin", IdentifierType.GTIN),
        ("mpn", IdentifierType.MPN),
        ("epid", IdentifierType.SKU),
    ):
        value = payload.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            identifiers.append(RawIdentifier(identifier_type=kind.value, value=str(value)))

    attributes: dict[str, Any] = {}
    if payload.get("itemGroupType"):
        # A listing that is a group of variants is not one sellable unit, and
        # the variation engine must be able to see that.
        attributes["item_group_type"] = str(payload["itemGroupType"])
    if payload.get("buyingOptions"):
        attributes["buying_options"] = ",".join(payload["buyingOptions"])
    if not payload.get("shippingOptions"):
        attributes["shipping_cost_known"] = "false"
    for aspect in payload.get("localizedAspects") or []:
        name, value = aspect.get("name"), aspect.get("value")
        if name and value:
            attributes[str(name).strip().lower()] = str(value)

    seller = payload.get("seller") or {}
    image = payload.get("image") or {}

    return RawListing(
        marketplace=Marketplace.EBAY,
        external_id=item_id,
        title=str(payload.get("title") or "").strip(),
        brand=payload.get("brand"),
        manufacturer=payload.get("brand"),
        model=payload.get("mpn"),
        category=(payload.get("categories") or [{}])[0].get("categoryName")
        if payload.get("categories")
        else payload.get("categoryPath"),
        sku=payload.get("sku"),
        url=payload.get("itemWebUrl"),
        image_url=image.get("imageUrl"),
        condition=parse_condition(payload.get("condition") or payload.get("conditionId")),
        identifiers=tuple(identifiers),
        attributes=attributes,
        price=parse_amount(payload.get("price")),
        shipping=parse_shipping(payload),
        availability=(
            Availability.IN_STOCK
            if payload.get("estimatedAvailabilities") is None
            else _availability(payload)
        ),
        seller_count=None,
        offer_count=None,
        sales_rank=None,
        rank_category=None,
        review_count=None,
        rating=(
            Decimal(str(seller["feedbackPercentage"]))
            if seller.get("feedbackPercentage") not in (None, "")
            else None
        ),
        quantity_available=_quantity(payload),
        observed_at=utcnow(),
        provider=provider_slug,
    )


def _availability(payload: dict[str, Any]) -> Availability:
    blocks = payload.get("estimatedAvailabilities") or []
    if not blocks:
        return Availability.UNKNOWN
    status = str(blocks[0].get("estimatedAvailabilityStatus") or "").upper()
    return {
        "IN_STOCK": Availability.IN_STOCK,
        "LIMITED_STOCK": Availability.LIMITED,
        "OUT_OF_STOCK": Availability.OUT_OF_STOCK,
    }.get(status, Availability.UNKNOWN)


def _quantity(payload: dict[str, Any]) -> int | None:
    blocks = payload.get("estimatedAvailabilities") or []
    if not blocks:
        return None
    block = blocks[0]
    for key in ("estimatedAvailableQuantity", "availableQuantity"):
        if isinstance(block.get(key), int):
            return block[key]
    return None


def map_offer(payload: dict[str, Any]) -> RawOffer:
    """One competing listing, treated as an offer against the product."""
    seller = payload.get("seller") or {}
    price = parse_amount(payload.get("price")) or Decimal("0")
    return RawOffer(
        external_offer_id=str(payload.get("itemId") or "") or None,
        seller_id=seller.get("username"),
        seller_name=seller.get("username"),
        price=money(price),
        shipping=money(parse_shipping(payload)),
        condition=parse_condition(payload.get("condition")),
        fulfillment="seller",
        # eBay has no buy box. The caller marks the lowest landed offer, which is
        # the closest honest analogue, and that decision lives in get_offers
        # rather than being asserted here per item.
        is_buy_box=False,
        is_marketplace_seller=False,
        availability=_availability(payload),
        quantity_available=_quantity(payload),
        observed_at=utcnow(),
    )


class EbayProvider(MarketplaceProvider):
    slug = "ebay"
    marketplace = Marketplace.EBAY
    display_name = "eBay"
    is_live = True
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
            ProviderCapability.COMPETITION,
        }
    )

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        marketplace_id: str | None = None,
        environment: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._client_id = client_id if client_id is not None else settings.ebay_client_id
        self._client_secret = (
            client_secret if client_secret is not None else settings.ebay_client_secret
        )
        self.marketplace_id = marketplace_id or settings.ebay_marketplace_id
        self.environment = environment or settings.ebay_environment
        self.base_url = SANDBOX_HOST if self.environment == "sandbox" else PRODUCTION_HOST
        self._client = client
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    @property
    def configuration_note(self) -> str | None:
        if self.is_configured:
            return (
                f"Live against {self.environment} for {self.marketplace_id}. "
                "Free developer account, 5,000 calls per day by default."
            )
        return (
            "Not configured: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET. A free "
            "developer account at developer.ebay.com issues them instantly; no "
            "seller account and no per-request cost."
        )

    async def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=settings.provider_timeout_seconds
            )
        return self._client

    async def _access_token(self) -> str:
        """Fetch or reuse the application token.

        Guarded by a lock so a burst of concurrent calls performs one token
        exchange rather than one per caller.
        """
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"eBay provider is not configured. {self.configuration_note}",
                provider=self.slug,
            )
        now = utcnow()
        if (
            self._token
            and self._token_expires_at
            and now < ensure_utc(self._token_expires_at) - TOKEN_SAFETY_MARGIN
        ):
            return self._token

        async with self._token_lock:
            # Re-check: another caller may have refreshed while we waited.
            if (
                self._token
                and self._token_expires_at
                and utcnow() < ensure_utc(self._token_expires_at) - TOKEN_SAFETY_MARGIN
            ):
                return self._token

            client = await self._client_or_new()
            basic = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode()
            ).decode()
            response = await client.post(
                "/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=f"grant_type=client_credentials&scope={SCOPE}",
            )
            if response.status_code in (400, 401):
                raise ProviderNotConfiguredError(
                    "eBay rejected the client credentials. Check EBAY_CLIENT_ID and "
                    "EBAY_CLIENT_SECRET, and that they match the selected environment "
                    f"({self.environment}).",
                    provider=self.slug,
                )
            if response.status_code >= 400:
                raise ProviderError(
                    f"eBay token exchange returned HTTP {response.status_code}.",
                    provider=self.slug,
                )
            payload = response.json()
            self._token = payload["access_token"]
            self._token_expires_at = now + timedelta(
                seconds=int(payload.get("expires_in", 7200))
            )
            return self._token

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._access_token()
        client = await self._client_or_new()
        response = await client.get(
            path,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
                "Accept": "application/json",
            },
        )
        if response.status_code == 401:
            # The token expired early; drop it so the next call re-exchanges.
            self._token = None
            raise ProviderError("eBay rejected the access token.", provider=self.slug)
        if response.status_code == 404:
            raise NotFoundError("eBay returned no such item.", provider=self.slug)
        if response.status_code == 429:
            raise ProviderError(
                "eBay rate limit reached (429). The free tier allows 5,000 calls per day.",
                provider=self.slug,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"eBay returned HTTP {response.status_code}.", provider=self.slug
            )
        return response.json()

    async def search_products(self, query: str, *, limit: int = 20) -> RawSearchResult:
        payload = await self._request(
            "/buy/browse/v1/item_summary/search",
            {"q": query, "limit": min(limit, 200)},
        )
        summaries = payload.get("itemSummaries") or []
        return RawSearchResult(
            listings=tuple(map_listing(item, provider_slug=self.slug) for item in summaries),
            total_results=payload.get("total"),
            query=query,
            truncated=bool(payload.get("total", 0) > len(summaries)),
        )

    async def get_product(self, external_id: str) -> RawListing:
        payload = await self._request(f"/buy/browse/v1/item/{external_id}")
        return map_listing(payload, provider_slug=self.slug)

    async def get_product_by_identifier(
        self, identifier_type: str, value: str
    ) -> RawListing | None:
        """Look a product up by GTIN.

        This is the reason eBay is valuable here: the same UPC that identified
        the product at the retailer identifies it on the marketplace, so the
        match rests on an identifier rather than on title text.
        """
        if identifier_type in {
            IdentifierType.GTIN.value,
            IdentifierType.UPC.value,
            IdentifierType.EAN.value,
            IdentifierType.ISBN.value,
        }:
            payload = await self._request(
                "/buy/browse/v1/item_summary/search", {"gtin": value, "limit": 10}
            )
        elif identifier_type in {IdentifierType.MPN.value, IdentifierType.MODEL.value}:
            payload = await self._request(
                "/buy/browse/v1/item_summary/search", {"q": value, "limit": 10}
            )
        elif identifier_type == IdentifierType.EBAY_ITEM_ID.value:
            return await self.get_product(value)
        else:
            return None

        summaries = payload.get("itemSummaries") or []
        if not summaries:
            return None
        # The cheapest landed listing is the one an operator would actually buy,
        # and the one whose price the economics should be computed against.
        best = min(
            summaries,
            key=lambda item: (parse_amount(item.get("price")) or Decimal("999999"))
            + parse_shipping(item),
        )
        return map_listing(best, provider_slug=self.slug)

    async def get_price(self, external_id: str) -> RawPricePoint:
        listing = await self.get_product(external_id)
        return RawPricePoint(
            price=money(listing.price or Decimal("0")),
            shipping=money(listing.shipping or Decimal("0")),
            availability=listing.availability,
            condition=listing.condition,
            is_buy_box=False,
            seller_id=None,
            seller_name=None,
            observed_at=listing.observed_at,
        )

    async def get_offers(self, external_id: str) -> tuple[RawOffer, ...]:
        """Competing listings for the same product, as an offer set.

        eBay has no single product page with an offer list, so the offer set is
        reconstructed: find the item, then search its GTIN for every concurrent
        listing of the same product. Without a GTIN there is no defensible way to
        say two listings are the same unit, so no offers are returned rather than
        a guess assembled from title matches.
        """
        listing = await self.get_product(external_id)
        gtin = next(
            (
                item.value
                for item in listing.identifiers
                if item.identifier_type
                in {IdentifierType.GTIN.value, IdentifierType.UPC.value}
            ),
            None,
        )
        if not gtin:
            return ()
        payload = await self._request(
            "/buy/browse/v1/item_summary/search", {"gtin": gtin, "limit": 50}
        )
        summaries = payload.get("itemSummaries") or []
        offers = [map_offer(item) for item in summaries]
        if not offers:
            return ()
        # Mark the lowest landed offer, the closest honest analogue to a buy box.
        cheapest = min(offers, key=lambda offer: offer.landed_price)
        return tuple(
            offer.model_copy(update={"is_buy_box": offer is cheapest}) for offer in offers
        )

    async def get_inventory(self, external_id: str) -> RawInventory:
        listing = await self.get_product(external_id)
        return RawInventory(
            availability=listing.availability,
            quantity_available=listing.quantity_available,
            max_order_quantity=listing.quantity_available,
            observed_at=listing.observed_at,
        )

    async def get_competition(
        self, external_id: str, *, days: int = 30
    ) -> tuple[RawCompetitionPoint, ...]:
        """A single competition observation, measured now.

        eBay exposes no history, so this returns one point rather than a
        synthesised series. Spreadline accumulates the trend itself by storing
        one observation per poll, which is honest about what is measured and
        what is merely inferred.
        """
        offers = await self.get_offers(external_id)
        if not offers:
            return ()
        landed = sorted(offer.landed_price for offer in offers)
        sellers = tuple({offer.seller_id for offer in offers if offer.seller_id})
        cheapest = min(offers, key=lambda offer: offer.landed_price)
        return (
            RawCompetitionPoint(
                seller_count=len(sellers) or len(offers),
                offer_count=len(offers),
                lowest_price=money(landed[0]),
                median_price=money(statistics.median(landed)),
                highest_price=money(landed[-1]),
                buy_box_price=money(cheapest.landed_price),
                buy_box_seller_id=cheapest.seller_id,
                marketplace_is_seller=False,
                seller_ids=sellers,
                observed_at=utcnow(),
            ),
        )

    async def health_check(self) -> bool:
        if not self.is_configured:
            return False
        try:
            await self._access_token()
            return True
        except Exception:  # noqa: BLE001 - a health check must not raise
            return False

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
