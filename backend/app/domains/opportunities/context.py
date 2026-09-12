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

from app.core.clock import utcnow
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
    #: True only when every provider that contributed reports live market data.
    is_live_data: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def weight_lb(self) -> Decimal | None:
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "brand": self.brand,
            "category": self.category,
            "direction": self.direction.value,
            "sourcing_channel": self.sourcing_channel.value,
            "analyzed_at": self.analyzed_at.isoformat(),
            "is_live_data": self.is_live_data,
            "source": {
                "marketplace": self.source.marketplace.value,
                "external_id": self.source.external_id,
                "price": None if self.source.price is None else str(self.source.price),
                "availability": self.source.availability.value,
                "quantity_available": self.source.quantity_available,
                "url": self.source.url,
            },
            "target": {
                "marketplace": self.target.marketplace.value,
                "external_id": self.target.external_id,
                "price": None if self.target.price is None else str(self.target.price),
                "availability": self.target.availability.value,
                "seller_count": self.target.seller_count,
                "url": self.target.url,
            },
            "warnings": self.warnings,
        }
