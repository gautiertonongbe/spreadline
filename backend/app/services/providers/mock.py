"""MockProvider.

A deterministic, offline provider over the fixture catalogue. It exists so the
whole platform, including identity resolution, price statistics, competition
analysis, scoring and the decision engine, can be developed and tested without
credentials and without a single live request.

It is honest about what it is: ``is_live`` is False, every response it produces is
tagged with the provider slug ``mock``, and where a fixture has no demand or no
history it returns nothing rather than inventing a plausible number.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import utcnow
from app.core.money import money
from app.domains.identity.similarity import title_similarity
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawCompetitionPoint,
    RawDemandPoint,
    RawIdentifier,
    RawInventory,
    RawListing,
    RawOffer,
    RawPricePoint,
    RawSearchResult,
)
from app.services.providers.fixtures import (
    FIXTURES,
    FIXTURES_BY_EXTERNAL_ID,
    FixtureListing,
    FixtureProduct,
    deterministic_unit,
    listing_upc,
)


class MockProvider(MarketplaceProvider):
    """Fixture-backed provider for one marketplace."""

    is_live = False
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
            ProviderCapability.HISTORY,
            ProviderCapability.DEMAND,
            ProviderCapability.COMPETITION,
        }
    )

    def __init__(self, marketplace: Marketplace = Marketplace.AMAZON, *, slug: str | None = None):
        self.marketplace = marketplace
        self.slug = slug or f"mock_{marketplace.value}"
        self.display_name = f"Mock {marketplace.value.title()}"

    @property
    def configuration_note(self) -> str | None:
        return "Fixture data. Not a market observation."

    # -- lookup -------------------------------------------------------------

    def _find(self, external_id: str) -> tuple[FixtureProduct, FixtureListing]:
        entry = FIXTURES_BY_EXTERNAL_ID.get(self.marketplace, {}).get(external_id)
        if entry is None:
            from app.core.errors import NotFoundError

            raise NotFoundError(
                f"No listing {external_id} on {self.marketplace.value}.",
                provider=self.slug,
                external_id=external_id,
            )
        return entry

    def _to_listing(
        self, fixture: FixtureProduct, listing: FixtureListing, *, observed_at=None
    ) -> RawListing:
        identifiers: list[RawIdentifier] = []
        upc = listing_upc(fixture, listing)
        if upc:
            identifiers.append(RawIdentifier(identifier_type=IdentifierType.UPC.value, value=upc))
        mpn = listing.mpn or fixture.mpn
        if mpn:
            identifiers.append(RawIdentifier(identifier_type=IdentifierType.MPN.value, value=mpn))
        identifiers.append(
            RawIdentifier(
                identifier_type=(
                    IdentifierType.ASIN.value
                    if self.marketplace is Marketplace.AMAZON
                    else IdentifierType.WALMART_ITEM_ID.value
                ),
                value=listing.external_id,
            )
        )

        attributes = dict(listing.attributes)
        if listing.pack_count:
            attributes["pack_count"] = str(listing.pack_count)
        attributes.setdefault("weight_lb", str(fixture.weight_lb))
        if fixture.cubic_feet is not None:
            attributes.setdefault("cubic_feet", str(fixture.cubic_feet))
        attributes["fixture_scenario"] = fixture.scenario

        return RawListing(
            marketplace=self.marketplace,
            external_id=listing.external_id,
            title=listing.title,
            brand=fixture.brand,
            manufacturer=fixture.brand,
            model=fixture.model,
            category=fixture.category,
            url=f"https://example.invalid/{self.marketplace.value}/{listing.external_id}",
            condition=Condition(listing.condition),
            identifiers=tuple(identifiers),
            attributes=attributes,
            price=money(listing.price),
            shipping=money(listing.shipping),
            availability=listing.availability,
            seller_count=listing.seller_count,
            offer_count=listing.offer_count,
            sales_rank=listing.sales_rank,
            rank_category=listing.rank_category,
            review_count=listing.review_count,
            rating=listing.rating,
            quantity_available=listing.quantity_available,
            observed_at=observed_at or utcnow(),
            provider=self.slug,
        )

    # -- capabilities -------------------------------------------------------

    async def search_products(self, query: str, *, limit: int = 20) -> RawSearchResult:
        scored: list[tuple[float, FixtureProduct, FixtureListing]] = []
        needle = query.strip().lower()
        for fixture in FIXTURES:
            listing = fixture.listing(self.marketplace)
            if listing is None:
                continue
            # An identifier or an external id in the query box is an exact lookup,
            # not a search; operators paste them constantly.
            if needle in {
                listing.external_id.lower(),
                (listing_upc(fixture, listing) or "").lower(),
            }:
                scored.append((1.0, fixture, listing))
                continue
            score = title_similarity(query, listing.title)
            if needle and needle in listing.title.lower():
                score = max(score, 0.9)
            if score >= 0.45:
                scored.append((score, fixture, listing))
        scored.sort(key=lambda row: row[0], reverse=True)
        selected = scored[:limit]
        return RawSearchResult(
            listings=tuple(self._to_listing(fixture, listing) for _, fixture, listing in selected),
            total_results=len(scored),
            query=query,
            truncated=len(scored) > limit,
        )

    async def get_product(self, external_id: str) -> RawListing:
        fixture, listing = self._find(external_id)
        return self._to_listing(fixture, listing)

    async def get_product_by_identifier(
        self, identifier_type: str, value: str
    ) -> RawListing | None:
        from app.domains.identity.normalization import normalize_gtin, normalize_mpn

        wanted_gtin = normalize_gtin(value)
        wanted_mpn = normalize_mpn(value)
        for fixture in FIXTURES:
            listing = fixture.listing(self.marketplace)
            if listing is None:
                continue
            upc = listing_upc(fixture, listing)
            if wanted_gtin and upc and normalize_gtin(upc) == wanted_gtin:
                return self._to_listing(fixture, listing)
            mpn = listing.mpn or fixture.mpn
            if wanted_mpn and mpn and normalize_mpn(mpn) == wanted_mpn:
                return self._to_listing(fixture, listing)
            if value.strip().upper() == listing.external_id.upper():
                return self._to_listing(fixture, listing)
        return None

    async def get_price(self, external_id: str) -> RawPricePoint:
        _, listing = self._find(external_id)
        return RawPricePoint(
            price=money(listing.price),
            shipping=money(listing.shipping),
            availability=listing.availability,
            condition=Condition(listing.condition),
            is_buy_box=True,
            seller_id=self._buy_box_seller(listing),
            seller_name=self._buy_box_seller(listing),
            observed_at=utcnow(),
        )

    async def get_offers(self, external_id: str) -> tuple[RawOffer, ...]:
        fixture, listing = self._find(external_id)
        now = utcnow()
        count = listing.seller_count or 1
        base = money(listing.price)
        offers: list[RawOffer] = []
        for index in range(count):
            # Offer dispersion widens with seller count: crowded listings have
            # long tails of optimistic pricing above the buy box.
            spread = Decimal(str(round(deterministic_unit(listing.external_id, "offer", index), 4)))
            premium = Decimal("0") if index == 0 else (spread * Decimal("0.18") + Decimal("0.01"))
            price = money(base * (Decimal("1") + premium))
            seller_is_marketplace = index == 0 and (
                (fixture.brand or "").lower() == self.marketplace.value
            )
            offers.append(
                RawOffer(
                    external_offer_id=f"{listing.external_id}-O{index}",
                    seller_id=(
                        self._buy_box_seller(listing)
                        if index == 0
                        else f"SELLER-{listing.external_id[-4:]}-{index}"
                    ),
                    seller_name=(
                        self.marketplace.value.title()
                        if seller_is_marketplace
                        else f"Seller {index + 1}"
                    ),
                    price=price,
                    shipping=money(listing.shipping) if index == 0 else money(Decimal("0")),
                    condition=Condition(listing.condition),
                    fulfillment="fba" if self.marketplace is Marketplace.AMAZON else "wfs",
                    is_buy_box=index == 0,
                    is_marketplace_seller=seller_is_marketplace,
                    availability=listing.availability,
                    quantity_available=(listing.quantity_available if index == 0 else None),
                    observed_at=now,
                )
            )
        return tuple(offers)

    async def get_inventory(self, external_id: str) -> RawInventory:
        _, listing = self._find(external_id)
        return RawInventory(
            availability=listing.availability,
            quantity_available=listing.quantity_available,
            max_order_quantity=listing.quantity_available,
            observed_at=utcnow(),
        )

    async def get_history(self, external_id: str, *, days: int = 90) -> tuple[RawPricePoint, ...]:
        _, listing = self._find(external_id)
        available = min(days, listing.history_days)
        if available <= 0:
            return ()
        now = utcnow()
        current = money(listing.price)
        # When the current price is a shock, history is generated around the
        # pre-shock level so the anomaly detector has something real to find.
        base = (
            money(current / (Decimal("1") + listing.latest_shock))
            if listing.latest_shock
            else current
        )
        points: list[RawPricePoint] = []
        for offset in range(available - 1, 0, -1):
            progress = Decimal(str((available - offset) / available))
            noise = Decimal(str(round(deterministic_unit(listing.external_id, "px", offset), 4)))
            drift = listing.trend * progress
            wobble = (noise - Decimal("0.5")) * 2 * listing.volatility
            price = money(base * (Decimal("1") + drift + wobble))
            if price <= 0:
                price = money(base * Decimal("0.5"))
            points.append(
                RawPricePoint(
                    price=price,
                    shipping=money(listing.shipping),
                    availability=Availability.IN_STOCK,
                    condition=Condition(listing.condition),
                    is_buy_box=True,
                    seller_id=self._buy_box_seller(listing),
                    observed_at=now - timedelta(days=offset),
                )
            )
        points.append(
            RawPricePoint(
                price=current,
                shipping=money(listing.shipping),
                availability=listing.availability,
                condition=Condition(listing.condition),
                is_buy_box=True,
                seller_id=self._buy_box_seller(listing),
                observed_at=now,
            )
        )
        return tuple(points)

    async def get_demand(self, external_id: str, *, days: int = 90) -> tuple[RawDemandPoint, ...]:
        _, listing = self._find(external_id)
        # A fixture with no demand data returns nothing. Downstream this becomes
        # "demand confidence: none", never "demand: zero".
        if not listing.demand_available or listing.sales_rank is None:
            return ()
        available = min(days, listing.history_days)
        if available <= 0:
            return ()
        now = utcnow()
        points: list[RawDemandPoint] = []
        for offset in range(available - 1, -1, -1):
            noise = deterministic_unit(listing.external_id, "rank", offset)
            rank = max(1, int(listing.sales_rank * (0.8 + 0.4 * noise)))
            reviews = (
                None
                if listing.review_count is None
                else max(0, int(listing.review_count * (1 - 0.0006 * offset)))
            )
            points.append(
                RawDemandPoint(
                    sales_rank=rank,
                    rank_category=listing.rank_category,
                    review_count=reviews,
                    rating=listing.rating,
                    #: MockProvider supplies no unit estimate. Converting a rank
                    #: to units needs a per-category calibration the platform does
                    #: not have, and a fixture must not pretend otherwise.
                    estimated_monthly_units=None,
                    estimation_basis=None,
                    buy_box_seller_id=self._buy_box_seller(listing),
                    observed_at=now - timedelta(days=offset),
                )
            )
        return tuple(points)

    async def get_competition(
        self, external_id: str, *, days: int = 30
    ) -> tuple[RawCompetitionPoint, ...]:
        fixture, listing = self._find(external_id)
        available = min(days, listing.history_days)
        if available <= 0 or listing.seller_count is None:
            return ()
        now = utcnow()
        end = listing.seller_count
        start = listing.seller_count_start if listing.seller_count_start is not None else end
        points: list[RawCompetitionPoint] = []
        for offset in range(available - 1, -1, -1):
            progress = (available - 1 - offset) / max(1, available - 1)
            count = max(1, round(start + (end - start) * progress))
            base = money(listing.price)
            noise = Decimal(str(round(deterministic_unit(listing.external_id, "comp", offset), 4)))
            lowest = money(base * (Decimal("0.97") + noise * Decimal("0.03")))
            points.append(
                RawCompetitionPoint(
                    seller_count=count,
                    offer_count=count
                    + (listing.offer_count or count)
                    - (listing.seller_count or count),
                    lowest_price=lowest,
                    median_price=money(lowest * Decimal("1.04")),
                    highest_price=money(lowest * Decimal("1.18")),
                    buy_box_price=base,
                    buy_box_seller_id=self._buy_box_seller(listing),
                    marketplace_is_seller=(fixture.brand or "").lower() == self.marketplace.value,
                    seller_ids=tuple(
                        f"SELLER-{listing.external_id[-4:]}-{index}" for index in range(count)
                    ),
                    observed_at=now - timedelta(days=offset),
                )
            )
        return tuple(points)

    async def health_check(self) -> bool:
        return True

    def _buy_box_seller(self, listing: FixtureListing) -> str:
        return f"BB-{listing.external_id[-6:]}"


def mock_providers() -> list[MockProvider]:
    return [MockProvider(Marketplace.AMAZON), MockProvider(Marketplace.WALMART)]
