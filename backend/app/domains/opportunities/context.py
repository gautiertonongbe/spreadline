"""The analysis context.

One object carrying everything the engines produced about a single candidate
opportunity. Risk, scoring, decision and stress testing all read from it, which
keeps them independent of each other and of how the data was fetched: the same
context can come from a live analysis, from a seeded fixture or from a historical
reconstruction during a backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.clock import iso_utc, utcnow
from app.domains.competition.service import CompetitionAssessment
from app.domains.demand.service import DemandAssessment
from app.domains.identity.matcher import MatchResult
from app.domains.pricing.anomaly import PriceAnomaly
from app.domains.pricing.statistics import PriceHistoryAnalysis
from app.domains.profitability.engine import ProfitabilityResult
from app.domains.quality.service import DataQualityScore
from app.models.enums import Availability, Direction, Marketplace, SourcingChannel


@dataclass
class ListingContext:
    """One side of the spread."""

    listing_id: str | None
    marketplace: Marketplace
    external_id: str
    title: str
    price: Decimal | None
    shipping: Decimal = Decimal("0")
    availability: Availability = Availability.UNKNOWN
    quantity_available: int | None = None
    seller_count: int | None = None
    url: str | None = None
    provider: str | None = None
    is_live_data: bool = False
    observed_at: datetime | None = None

    @property
    def landed_price(self) -> Decimal | None:
        if self.price is None:
            return None
        return self.price + (self.shipping or Decimal("0"))


@dataclass
class AnalysisContext:
    """Everything known about one candidate, from every domain."""

    product_id: str | None
    title: str
    brand: str | None
    category: str | None
    source: ListingContext
    target: ListingContext
    match: MatchResult
    profitability: ProfitabilityResult
    quality: DataQualityScore
    source_prices: PriceHistoryAnalysis
    target_prices: PriceHistoryAnalysis
    source_anomaly: PriceAnomaly
    target_anomaly: PriceAnomaly
    demand: DemandAssessment
    competition: CompetitionAssessment
    direction: Direction = Direction.CUSTOM
    sourcing_channel: SourcingChannel = SourcingChannel.ONLINE_ARBITRAGE
    analyzed_at: datetime = field(default_factory=utcnow)
    #: Physical attributes, carried so that any recomputation (a stress
    #: scenario, a what-if) prices the same unit as the base case. Without them
    #: a scenario silently falls back to the default weight and volume, and a
    #: "downside" can come out cheaper to fulfil than the case it is stressing.
    weight_lb: Decimal | None = None
    cubic_feet: Decimal | None = None
    #: True only when every provider that contributed reports live market data.
    is_live_data: bool = False
    warnings: list[str] = field(default_factory=list)

    def provenance(self) -> list[dict[str, Any]]:
        """Where each price came from, and whether it is a market observation.

        Carried on every analysis so that when real providers are connected the
        reader can tell, per side, which adapter answered, when it last
        answered, and whether it was a live call or fixture data. Today both
        sides are fixtures and the block says so; nothing about the shape
        changes when they are not, which is the point of publishing it now.
        """
        return [
            {
                "role": role,
                "marketplace": side.marketplace.value,
                "external_id": side.external_id,
                "provider": side.provider,
                "is_live_data": side.is_live_data,
                "observed_at": iso_utc(side.observed_at),
            }
            for role, side in (("source", self.source), ("target", self.target))
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "brand": self.brand,
            "category": self.category,
            "direction": self.direction.value,
            "sourcing_channel": self.sourcing_channel.value,
            "analyzed_at": iso_utc(self.analyzed_at),
            "is_live_data": self.is_live_data,
            "source": {
                "marketplace": self.source.marketplace.value,
                "external_id": self.source.external_id,
                "price": None if self.source.price is None else str(self.source.price),
                "availability": self.source.availability.value,
                "quantity_available": self.source.quantity_available,
                "url": self.source.url,
                "provider": self.source.provider,
                "is_live_data": self.source.is_live_data,
            },
            "target": {
                "marketplace": self.target.marketplace.value,
                "external_id": self.target.external_id,
                "price": None if self.target.price is None else str(self.target.price),
                "availability": self.target.availability.value,
                "seller_count": self.target.seller_count,
                "url": self.target.url,
                "provider": self.target.provider,
                "is_live_data": self.target.is_live_data,
            },
            "provenance": self.provenance(),
            "warnings": self.warnings,
        }
