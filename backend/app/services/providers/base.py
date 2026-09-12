"""Provider interface and the provider-agnostic DTOs that cross it.

Nothing above this layer knows whether data came from Amazon, Walmart, a licensed
aggregator or a fixture file. Providers change, disappear, get repriced and cover
different fields; the rest of Spreadline is written against this interface so that
swapping one is an adapter change (spec §6).

Two rules hold everywhere in this package:

1. A provider that cannot answer raises ``ProviderCapabilityError`` rather than
   returning an empty or invented result. "No demand data" and "demand of zero"
   are different facts and the distinction must survive the boundary.
2. A provider without credentials raises ``ProviderNotConfiguredError``. It never
   silently falls back to mock data, because a fake live integration is worse
   than a missing one.
"""

from __future__ import annotations

import abc
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Availability, Condition, Marketplace


class ProviderCapability(StrEnum):
    SEARCH = "search"
    PRODUCT = "product"
    PRICE = "price"
    OFFERS = "offers"
    INVENTORY = "inventory"
    HISTORY = "history"
    DEMAND = "demand"
    COMPETITION = "competition"


class ProviderDTO(BaseModel):
    """Base for boundary objects: immutable, strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RawIdentifier(ProviderDTO):
    identifier_type: str
    value: str


class RawOffer(ProviderDTO):
    external_offer_id: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    price: Decimal
    shipping: Decimal = Decimal("0")
    condition: Condition = Condition.NEW
    fulfillment: str | None = None
    is_buy_box: bool = False
    is_marketplace_seller: bool = False
    availability: Availability = Availability.UNKNOWN
    quantity_available: int | None = None
    observed_at: datetime

    @property
    def landed_price(self) -> Decimal:
        return self.price + self.shipping


class RawListing(ProviderDTO):
    """A marketplace listing exactly as the provider described it.

    ``attributes`` holds the provider's own key/values before normalisation. It is
    carried through and stored so that a normalisation bug is diagnosable against
    the source rather than against its lossy interpretation.
    """

    marketplace: Marketplace
    external_id: str
    title: str
    brand: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    category: str | None = None
    sku: str | None = None
    url: str | None = None
    image_url: str | None = None
    condition: Condition = Condition.NEW
    identifiers: tuple[RawIdentifier, ...] = ()
    attributes: dict[str, Any] = Field(default_factory=dict)

    price: Decimal | None = None
    shipping: Decimal | None = None
    availability: Availability = Availability.UNKNOWN
    seller_count: int | None = None
    offer_count: int | None = None
    sales_rank: int | None = None
    rank_category: str | None = None
    review_count: int | None = None
    rating: Decimal | None = None
    quantity_available: int | None = None

    observed_at: datetime
    provider: str


class RawPricePoint(ProviderDTO):
    price: Decimal
    shipping: Decimal = Decimal("0")
    availability: Availability = Availability.UNKNOWN
    condition: Condition = Condition.NEW
    is_buy_box: bool = False
    seller_id: str | None = None
    seller_name: str | None = None
    observed_at: datetime
    currency: str = "USD"


class RawDemandPoint(ProviderDTO):
    sales_rank: int | None = None
    rank_category: str | None = None
    review_count: int | None = None
    rating: Decimal | None = None
    #: Populated only when the provider itself supplies an estimate, with its
    #: basis named. Spreadline never derives units from a rank it cannot calibrate.
    estimated_monthly_units: int | None = None
    estimation_basis: str | None = None
    buy_box_seller_id: str | None = None
    observed_at: datetime


class RawInventory(ProviderDTO):
    availability: Availability = Availability.UNKNOWN
    quantity_available: int | None = None
    max_order_quantity: int | None = None
    observed_at: datetime


class RawCompetitionPoint(ProviderDTO):
    """Competition as of one instant.

    Spreadline normally derives this itself by storing repeated offer
    observations. The capability exists because some providers expose a seller
    count history directly, which is worth far more than waiting 30 days to
    accumulate one.
    """

    seller_count: int | None = None
    offer_count: int | None = None
    lowest_price: Decimal | None = None
    median_price: Decimal | None = None
    highest_price: Decimal | None = None
    buy_box_price: Decimal | None = None
    buy_box_seller_id: str | None = None
    marketplace_is_seller: bool | None = None
    seller_ids: tuple[str, ...] = ()
    observed_at: datetime


class RawSearchResult(ProviderDTO):
    listings: tuple[RawListing, ...] = ()
    total_results: int | None = None
    query: str | None = None
    truncated: bool = False


class ProviderInfo(ProviderDTO):
    slug: str
    marketplace: Marketplace
    display_name: str
    capabilities: tuple[ProviderCapability, ...]
    is_configured: bool
    is_live: bool
    kind: str = "live"
    #: Why a provider is unusable, in words fit to show an operator.
    configuration_note: str | None = None


class MarketplaceProvider(abc.ABC):
    """The only surface the rest of the platform is allowed to depend on."""

    slug: str
    marketplace: Marketplace
    display_name: str
    capabilities: frozenset[ProviderCapability] = frozenset()
    #: False for fixtures. Surfaced in the UI so a mock result is never mistaken
    #: for a market observation.
    is_live: bool = False
    #: What this provider actually is. "live" calls a market, "fixture" serves
    #: canned data, "planned" is registered but unimplemented. Three states
    #: rather than one boolean, because an unimplemented platform and a fixture
    #: are different things and labelling the first as the second is misleading.
    kind: str = "live"

    @property
    def is_configured(self) -> bool:
        """Whether this provider can actually be called."""
        return True

    @property
    def configuration_note(self) -> str | None:
        return None

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            slug=self.slug,
            marketplace=self.marketplace,
            display_name=self.display_name,
            capabilities=tuple(sorted(self.capabilities)),
            is_configured=self.is_configured,
            is_live=self.is_live,
            kind=self.kind,
            configuration_note=self.configuration_note,
        )

    # -- capability methods -------------------------------------------------
    # Defaults raise rather than return empty results. A provider opts in by
    # declaring the capability and overriding the method.

    async def search_products(self, query: str, *, limit: int = 20) -> RawSearchResult:
        raise self._unsupported(ProviderCapability.SEARCH)

    async def get_product(self, external_id: str) -> RawListing:
        raise self._unsupported(ProviderCapability.PRODUCT)

    async def get_product_by_identifier(
        self, identifier_type: str, value: str
    ) -> RawListing | None:
        """Look a listing up by GTIN/UPC/EAN/MPN.

        Returns None only for "the provider searched and found nothing", which is
        a real answer, unlike an unsupported capability.
        """
        raise self._unsupported(ProviderCapability.PRODUCT)

    async def get_price(self, external_id: str) -> RawPricePoint:
        raise self._unsupported(ProviderCapability.PRICE)

    async def get_offers(self, external_id: str) -> tuple[RawOffer, ...]:
        raise self._unsupported(ProviderCapability.OFFERS)

    async def get_inventory(self, external_id: str) -> RawInventory:
        raise self._unsupported(ProviderCapability.INVENTORY)

    async def get_history(self, external_id: str, *, days: int = 90) -> tuple[RawPricePoint, ...]:
        raise self._unsupported(ProviderCapability.HISTORY)

    async def get_demand(self, external_id: str, *, days: int = 90) -> tuple[RawDemandPoint, ...]:
        raise self._unsupported(ProviderCapability.DEMAND)

    async def get_competition(
        self, external_id: str, *, days: int = 30
    ) -> tuple[RawCompetitionPoint, ...]:
        raise self._unsupported(ProviderCapability.COMPETITION)

    async def health_check(self) -> bool:
        return self.is_configured

    async def close(self) -> None:  # noqa: B027 - concrete on purpose
        """Release connections.

        Deliberately concrete rather than abstract: most providers hold no
        resources, and forcing every one of them to implement an empty method
        adds ceremony without adding safety.
        """

    def _unsupported(self, capability: ProviderCapability) -> Exception:
        from app.core.errors import ProviderCapabilityError

        return ProviderCapabilityError(
            f"{self.display_name} does not support '{capability.value}'.",
            provider=self.slug,
            capability=capability.value,
        )
