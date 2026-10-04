"""The one place a provider answer becomes a stored observation.

Every legitimate provider response that describes a product on a marketplace
passes through here and leaves a timestamped snapshot behind. That is what makes
the dataset accumulate rather than exist only as a side effect of whichever
analysis happened to run.

Three rules, and they are the whole module:

1. **Nothing is invented.** A field the provider did not return is stored as
   null. Buy Box, seller count, sales rank and availability are all optional in
   the payload and optional in the row. A provider that cannot answer for a
   capability is not asked, and its silence is not recorded as a zero.
2. **Provenance travels with the row.** Which provider answered, when it was
   called, when the market showed the value, and whether the provider calls a
   market at all. A simulated observation is marked at capture, not inferred
   later from a slug.
3. **Capture never breaks the caller.** A failure to record history must not
   fail an analysis or a refresh. The observation is the by-product; losing one
   is a gap in a dataset, and raising here would turn it into a broken feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.domains.catalog import service as catalog
from app.models.catalog import MarketplaceListing
from app.services.providers.base import (
    RawCompetitionPoint,
    RawDemandPoint,
    RawListing,
    RawOffer,
    RawPricePoint,
)

logger = get_logger(__name__)

#: How the observation was obtained. Statistics weight these differently and an
#: audit has to be able to tell them apart.
SOURCE_POLL = "poll"
SOURCE_ANALYSIS = "analysis"
SOURCE_REFRESH = "refresh"
SOURCE_PROVIDER_HISTORY = "provider_history"


@dataclass
class CaptureResult:
    """What one capture actually wrote."""

    listing_id: str | None = None
    product_id: str | None = None
    price_observations: int = 0
    demand_observations: int = 0
    competition_observations: int = 0
    offers_recorded: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.price_observations
            + self.demand_observations
            + self.competition_observations
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "listing_id": self.listing_id,
            "product_id": self.product_id,
            "price_observations": self.price_observations,
            "demand_observations": self.demand_observations,
            "competition_observations": self.competition_observations,
            "offers_recorded": self.offers_recorded,
            "total": self.total,
            "errors": self.errors,
        }


def price_point_from(listing: RawListing) -> RawPricePoint | None:
    """The current price, as a point, or None when there is no price.

    A listing without a price is a real answer from some providers, and it must
    not become an observation of zero.
    """
    if listing.price is None:
        return None
    from app.models.enums import Availability, Condition

    return RawPricePoint(
        price=listing.price,
        shipping=listing.shipping or Decimal("0"),
        availability=listing.availability or Availability.UNKNOWN,
        condition=listing.condition or Condition.NEW,
        is_buy_box=False,
        observed_at=listing.observed_at or utcnow(),
    )


def capture_listing(
    session: Session,
    organization_id: str,
    listing: RawListing,
    *,
    provider: str,
    is_simulated: bool | None = None,
    source: str = SOURCE_POLL,
    price_points: list[RawPricePoint] | None = None,
    demand_points: list[RawDemandPoint] | None = None,
    competition_points: list[RawCompetitionPoint] | None = None,
    offers: list[RawOffer] | None = None,
    quality_score: Decimal | None = None,
    retrieved_at: datetime | None = None,
    row: MarketplaceListing | None = None,
) -> CaptureResult:
    """Persist one provider answer as history.

    ``row`` lets a caller that has already upserted the listing avoid doing it
    twice; everything else is optional because providers differ in what they can
    answer, and asking for what a provider does not have is how empty results
    get mistaken for measurements.
    """
    result = CaptureResult()
    try:
        stored = row or catalog.upsert_listing(session, organization_id, listing)
        catalog.resolve_product(session, organization_id, listing, stored)
        result.listing_id = stored.id
        result.product_id = stored.product_id

        stamp = catalog.observation_stamp(
            session,
            stored,
            provider=provider,
            is_simulated=is_simulated,
            quality_score=quality_score,
            retrieved_at=retrieved_at,
        )

        points = list(price_points or [])
        if not points:
            current = price_point_from(listing)
            if current is not None:
                points = [current]
        if points:
            result.price_observations = catalog.record_price_observations(
                session,
                organization_id,
                stored,
                points,
                provider=provider,
                source=source,
                stamp=stamp,
            )

        if demand_points:
            result.demand_observations = catalog.record_demand_observations(
                session,
                organization_id,
                stored,
                demand_points,
                provider=provider,
                source=source,
                stamp=stamp,
            )

        if competition_points:
            result.competition_observations = catalog.record_competition_observations(
                session,
                organization_id,
                stored,
                competition_points,
                provider=provider,
                source=source,
                stamp=stamp,
            )

        if offers:
            result.offers_recorded = catalog.replace_offers(
                session, organization_id, stored, offers, provider=provider
            )

    except Exception as exc:  # noqa: BLE001 - a lost observation is not a failed request
        # Deliberately swallowed and reported. Capture is a by-product of
        # answering a question; letting it fail the question would trade a gap
        # in a dataset for a broken feature.
        result.errors.append(str(exc))
        logger.warning(
            "observation capture failed",
            extra={
                "context": {
                    "provider": provider,
                    "marketplace": getattr(listing.marketplace, "value", listing.marketplace),
                    "external_id": listing.external_id,
                    "error": str(exc),
                }
            },
        )
    return result
