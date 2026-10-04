"""Catalog normalisation and persistence.

Turns provider DTOs into the canonical product graph and appends observations to
the history tables. Two rules shape the code:

* Listings are upserted on (organization, marketplace, external id). Re-analysing
  a product updates the listing in place rather than growing duplicates.
* History is append-only and de-duplicated per listing, at the granularity of
  the source: live polls by instant, provider history by calendar day. Re-running
  an analysis must not double the observation count and inflate every statistic
  that depends on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, utcnow
from app.core.money import money
from app.domains.identity.normalization import normalize_identifier
from app.models.catalog import MarketplaceListing, Offer, Product, ProductIdentifier
from app.models.enums import IdentifierType, Marketplace
from app.models.observations import (
    CompetitionObservation,
    DemandObservation,
    PriceObservation,
)
from app.services.providers.base import (
    RawCompetitionPoint,
    RawDemandPoint,
    RawListing,
    RawOffer,
    RawPricePoint,
)
from app.services.providers.registry import get_registry


def get_listing(
    session: Session, organization_id: str, marketplace: Marketplace | str, external_id: str
) -> MarketplaceListing | None:
    return session.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.organization_id == organization_id,
            MarketplaceListing.marketplace == Marketplace(marketplace).value,
            MarketplaceListing.external_id == external_id,
        )
    )


def upsert_listing(session: Session, organization_id: str, raw: RawListing) -> MarketplaceListing:
    """Create or refresh the listing row for a provider observation."""
    listing = get_listing(session, organization_id, raw.marketplace, raw.external_id)
    pack_count = _int_or_none(raw.attributes.get("pack_count"))

    if listing is None:
        listing = MarketplaceListing(
            organization_id=organization_id,
            marketplace=raw.marketplace.value,
            external_id=raw.external_id,
            title=raw.title,
        )
        session.add(listing)

    listing.title = raw.title
    listing.brand = raw.brand
    listing.category = raw.category
    listing.sku = raw.sku
    listing.url = raw.url
    listing.image_url = raw.image_url
    listing.condition = raw.condition.value
    listing.pack_count = pack_count
    listing.current_price = money(raw.price) if raw.price is not None else None
    listing.current_shipping = money(raw.shipping) if raw.shipping is not None else None
    listing.availability = raw.availability.value
    listing.seller_count = raw.seller_count
    listing.offer_count = raw.offer_count
    listing.sales_rank = raw.sales_rank
    listing.review_count = raw.review_count
    listing.rating = raw.rating
    listing.raw_attributes = dict(raw.attributes)
    listing.provider = raw.provider
    listing.last_seen_at = ensure_utc(raw.observed_at)
    session.flush()
    return listing


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def resolve_product(
    session: Session, organization_id: str, raw: RawListing, listing: MarketplaceListing
) -> Product:
    """Find or create the canonical product this listing represents.

    Lookup is by validated GTIN first. Without one, the listing gets its own
    canonical product: merging two listings into one product is the identity
    engine's decision, taken with evidence, not a side effect of ingestion.
    """
    gtins = [
        normalized.normalized
        for normalized in (
            normalize_identifier(identifier.identifier_type, identifier.value)
            for identifier in raw.identifiers
        )
        if normalized.is_valid
        and normalized.normalized
        and normalized.identifier_type
        in {IdentifierType.GTIN, IdentifierType.UPC, IdentifierType.EAN, IdentifierType.ISBN}
    ]

    product: Product | None = None
    if listing.product_id:
        product = session.get(Product, listing.product_id)
    if product is None and gtins:
        product = session.scalar(
            select(Product).where(
                Product.organization_id == organization_id, Product.primary_gtin.in_(gtins)
            )
        )
    if product is None:
        product = Product(
            organization_id=organization_id,
            title=raw.title,
            brand=raw.brand,
            manufacturer=raw.manufacturer,
            model=raw.model,
            category=raw.category,
            primary_gtin=gtins[0] if gtins else None,
            pack_count=listing.pack_count,
            attributes=dict(raw.attributes),
        )
        session.add(product)
        session.flush()
    else:
        # Fill gaps without overwriting: the first provider to supply a field is
        # not automatically less right than the latest one.
        product.brand = product.brand or raw.brand
        product.manufacturer = product.manufacturer or raw.manufacturer
        product.model = product.model or raw.model
        product.category = product.category or raw.category
        product.primary_gtin = product.primary_gtin or (gtins[0] if gtins else None)
        product.pack_count = product.pack_count or listing.pack_count

    listing.product_id = product.id
    _sync_identifiers(session, organization_id, product, raw)
    session.flush()
    return product


def _sync_identifiers(
    session: Session, organization_id: str, product: Product, raw: RawListing
) -> None:
    existing = {
        (row.identifier_type, row.value)
        for row in session.scalars(
            select(ProductIdentifier).where(ProductIdentifier.product_id == product.id)
        )
    }
    for identifier in raw.identifiers:
        key = (identifier.identifier_type, identifier.value)
        if key in existing:
            continue
        normalized = normalize_identifier(identifier.identifier_type, identifier.value)
        session.add(
            ProductIdentifier(
                organization_id=organization_id,
                product_id=product.id,
                identifier_type=identifier.identifier_type,
                value=identifier.value,
                normalized_value=normalized.normalized,
                is_valid=normalized.is_valid,
                validation_note=normalized.note,
                source="provider",
                provider=raw.provider,
            )
        )
        existing.add(key)


#: Sources whose observations are de-duplicated by calendar day rather than by
#: exact instant. A provider's historical series is a daily series: re-requesting
#: it returns the same days, but rarely the same timestamps to the microsecond.
#: Matching on the instant would append the whole history again on every
#: re-analysis, silently doubling the observation count behind every statistic.
DAILY_SOURCES = frozenset({"provider_history", "backfill", "import"})


def _observation_key(observed_at: datetime, source: str) -> object:
    moment = ensure_utc(observed_at)
    return moment.date() if source in DAILY_SOURCES else moment


def _existing_observation_keys(
    session: Session, model: type, listing_id: str, source: str
) -> set[object]:
    """Existing observations, keyed at the granularity of the incoming source."""
    rows = session.scalars(select(model.observed_at).where(model.listing_id == listing_id))
    return {
        _observation_key(observed_at, source) for observed_at in rows if observed_at is not None
    }


@dataclass(frozen=True)
class ObservationStamp:
    """Identity and provenance attached to every observation at capture time.

    Resolved once per batch rather than per row: the identifier lookup is a
    query, and a history backfill can write hundreds of rows from one call.
    """

    external_id: str | None
    gtin: str | None
    asin: str | None
    is_simulated: bool
    retrieved_at: datetime
    quality_score: Decimal | None = None

    def as_columns(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "gtin": self.gtin,
            "asin": self.asin,
            "is_simulated": self.is_simulated,
            "retrieved_at": self.retrieved_at,
            "quality_score": self.quality_score,
        }


def observation_stamp(
    session: Session,
    listing: MarketplaceListing,
    *,
    provider: str,
    is_simulated: bool | None = None,
    quality_score: Decimal | None = None,
    retrieved_at: datetime | None = None,
) -> ObservationStamp:
    """Build the stamp for one listing.

    ``is_simulated`` is resolved through the provider registry when not given,
    because what a provider slug means is the registry's to say. A provider that
    is not registered is treated as simulated: assuming an unknown source was a
    real market observation is the error that would quietly contaminate the
    dataset, and the opposite error only understates it.
    """
    gtin: str | None = None
    asin: str | None = None
    if listing.product_id:
        for row in session.scalars(
            select(ProductIdentifier).where(ProductIdentifier.product_id == listing.product_id)
        ):
            value = row.normalized_value or row.value
            if row.identifier_type == IdentifierType.ASIN.value and asin is None:
                asin = value
            elif (
                row.identifier_type
                in {IdentifierType.GTIN.value, IdentifierType.UPC.value, IdentifierType.EAN.value}
                and gtin is None
                and row.is_valid
            ):
                gtin = value

    if is_simulated is None:
        try:
            is_simulated = not get_registry().get(provider).is_live
        except Exception:  # noqa: BLE001 - an unknown provider is not a live market
            is_simulated = True

    return ObservationStamp(
        external_id=listing.external_id,
        gtin=gtin,
        asin=asin,
        is_simulated=is_simulated,
        retrieved_at=retrieved_at or utcnow(),
        quality_score=quality_score,
    )


def record_price_observations(
    session: Session,
    organization_id: str,
    listing: MarketplaceListing,
    points: Sequence[RawPricePoint],
    *,
    provider: str,
    source: str = "poll",
    stamp: ObservationStamp | None = None,
) -> int:
    """Append price observations, skipping ones already stored."""
    if not points:
        return 0
    columns = (
        stamp or observation_stamp(session, listing, provider=provider)
    ).as_columns()
    seen = _existing_observation_keys(session, PriceObservation, listing.id, source)
    added = 0
    for point in points:
        observed_at = ensure_utc(point.observed_at)
        key = _observation_key(observed_at, source)
        if key in seen:
            continue
        session.add(
            PriceObservation(
                **columns,
                organization_id=organization_id,
                product_id=listing.product_id,
                listing_id=listing.id,
                marketplace=listing.marketplace,
                price=money(point.price),
                shipping=money(point.shipping),
                landed_price=money(point.price + point.shipping),
                currency=point.currency,
                availability=point.availability.value,
                condition=point.condition.value,
                is_buy_box=point.is_buy_box,
                seller_id=point.seller_id,
                seller_name=point.seller_name,
                observed_at=observed_at,
                provider=provider,
                source=source,
            )
        )
        seen.add(key)
        added += 1
    session.flush()
    return added


def record_demand_observations(
    session: Session,
    organization_id: str,
    listing: MarketplaceListing,
    points: Sequence[RawDemandPoint],
    *,
    provider: str,
    source: str = "poll",
    stamp: ObservationStamp | None = None,
) -> int:
    if not points:
        return 0
    columns = (
        stamp or observation_stamp(session, listing, provider=provider)
    ).as_columns()
    seen = _existing_observation_keys(session, DemandObservation, listing.id, source)
    added = 0
    for point in points:
        observed_at = ensure_utc(point.observed_at)
        key = _observation_key(observed_at, source)
        if key in seen:
            continue
        session.add(
            DemandObservation(
                **columns,
                organization_id=organization_id,
                product_id=listing.product_id,
                listing_id=listing.id,
                marketplace=listing.marketplace,
                sales_rank=point.sales_rank,
                rank_category=point.rank_category,
                estimated_monthly_units=point.estimated_monthly_units,
                estimation_basis=point.estimation_basis,
                review_count=point.review_count,
                rating=point.rating,
                buy_box_seller_id=point.buy_box_seller_id,
                observed_at=observed_at,
                provider=provider,
                source=source,
            )
        )
        seen.add(key)
        added += 1
    session.flush()
    return added


def record_competition_observations(
    session: Session,
    organization_id: str,
    listing: MarketplaceListing,
    points: Sequence[RawCompetitionPoint],
    *,
    provider: str,
    source: str = "poll",
    stamp: ObservationStamp | None = None,
) -> int:
    if not points:
        return 0
    columns = (
        stamp or observation_stamp(session, listing, provider=provider)
    ).as_columns()
    seen = _existing_observation_keys(session, CompetitionObservation, listing.id, source)
    added = 0
    for point in points:
        observed_at = ensure_utc(point.observed_at)
        key = _observation_key(observed_at, source)
        if key in seen:
            continue
        session.add(
            CompetitionObservation(
                **columns,
                organization_id=organization_id,
                product_id=listing.product_id,
                listing_id=listing.id,
                marketplace=listing.marketplace,
                seller_count=point.seller_count,
                offer_count=point.offer_count,
                lowest_price=point.lowest_price,
                median_price=point.median_price,
                highest_price=point.highest_price,
                buy_box_price=point.buy_box_price,
                buy_box_seller_id=point.buy_box_seller_id,
                marketplace_is_seller=point.marketplace_is_seller,
                seller_ids=list(point.seller_ids),
                observed_at=observed_at,
                provider=provider,
                source=source,
            )
        )
        seen.add(key)
        added += 1
    session.flush()
    return added


def replace_offers(
    session: Session,
    organization_id: str,
    listing: MarketplaceListing,
    offers: Sequence[RawOffer],
    *,
    provider: str,
) -> list[Offer]:
    """Store the current offer set for a listing.

    Offers are replaced rather than accumulated: the competition_history table is
    the record of how the offer set changed over time, and keeping every offer
    row forever would make "who is on this listing now" an expensive question.
    """
    if not offers:
        return []
    existing = session.scalars(select(Offer).where(Offer.listing_id == listing.id)).all()
    for row in existing:
        session.delete(row)
    session.flush()

    rows: list[Offer] = []
    for offer in offers:
        row = Offer(
            organization_id=organization_id,
            listing_id=listing.id,
            external_offer_id=offer.external_offer_id,
            seller_id=offer.seller_id,
            seller_name=offer.seller_name,
            price=money(offer.price),
            shipping=money(offer.shipping),
            condition=offer.condition.value,
            fulfillment=offer.fulfillment,
            is_buy_box=offer.is_buy_box,
            is_marketplace_seller=offer.is_marketplace_seller,
            availability=offer.availability.value,
            quantity_available=offer.quantity_available,
            observed_at=ensure_utc(offer.observed_at),
            provider=provider,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def snapshot_current_price(
    session: Session,
    organization_id: str,
    listing: MarketplaceListing,
    *,
    provider: str,
    stamp: ObservationStamp | None = None,
    source: str = "poll",
) -> int:
    """Record the listing's current price as an observation."""
    if listing.current_price is None:
        return 0
    shipping = listing.current_shipping or Decimal("0")
    return record_price_observations(
        session,
        organization_id,
        listing,
        [
            RawPricePoint(
                price=listing.current_price,
                shipping=shipping,
                availability=_availability(listing.availability),
                observed_at=listing.last_seen_at or utcnow(),
            )
        ],
        provider=provider,
        source=source,
        stamp=stamp,
    )


def _availability(value: str):  # type: ignore[no-untyped-def]
    from app.models.enums import Availability

    try:
        return Availability(value)
    except ValueError:
        return Availability.UNKNOWN
