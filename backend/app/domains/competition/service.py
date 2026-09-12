"""Competition intelligence.

Seller count on its own says very little. Seventeen sellers on a listing that
had nine a month ago is a different proposition from seventeen that has been
stable for a year, and both are different from three sellers where one of them is
the marketplace itself (spec §13).

This module measures the level, the direction and the structure of competition,
and names the specific reason whenever it calls the risk high.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.clock import ensure_utc, utcnow
from app.core.money import money, ratio
from app.models.enums import Confidence, RiskLevel, TrendDirection

#: Seller count at which a listing is treated as crowded.
CROWDED_SELLER_COUNT = 10
#: Growth in seller count that counts as competition arriving.
SELLER_GROWTH_THRESHOLD = Decimal("0.30")
MIN_POINTS_FOR_TREND = 3


@dataclass(frozen=True)
class CompetitionPoint:
    observed_at: datetime
    seller_count: int | None = None
    offer_count: int | None = None
    lowest_price: Decimal | None = None
    median_price: Decimal | None = None
    buy_box_price: Decimal | None = None
    buy_box_seller_id: str | None = None
    marketplace_is_seller: bool | None = None
    seller_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OfferSnapshot:
    price: Decimal
    shipping: Decimal = Decimal("0")
    seller_id: str | None = None
    is_buy_box: bool = False
    is_marketplace_seller: bool = False

    @property
    def landed(self) -> Decimal:
        return self.price + self.shipping


@dataclass
class CompetitionAssessment:
    seller_count: int | None
    offer_count: int | None
    average_seller_count_30d: Decimal | None
    seller_trend: TrendDirection
    seller_change_pct: Decimal | None
    entrants_30d: int | None
    exits_30d: int | None
    buy_box_price: Decimal | None
    lowest_offer: Decimal | None
    median_offer: Decimal | None
    highest_offer: Decimal | None
    #: (highest - lowest) / median. Wide dispersion means the buy box is
    #: contested and the headline price is not what a new seller will get.
    price_dispersion: Decimal | None
    marketplace_is_seller: bool
    risk_level: RiskLevel
    #: 0..100, higher is more competitive pressure.
    pressure_score: Decimal
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "seller_count": self.seller_count,
            "offer_count": self.offer_count,
            "average_seller_count_30d": num(self.average_seller_count_30d),
            "seller_trend": self.seller_trend.value,
            "seller_change_pct": num(self.seller_change_pct),
            "entrants_30d": self.entrants_30d,
            "exits_30d": self.exits_30d,
            "buy_box_price": num(self.buy_box_price),
            "lowest_offer": num(self.lowest_offer),
            "median_offer": num(self.median_offer),
            "highest_offer": num(self.highest_offer),
            "price_dispersion": num(self.price_dispersion),
            "marketplace_is_seller": self.marketplace_is_seller,
            "risk_level": self.risk_level.value,
            "pressure_score": str(self.pressure_score),
            "confidence": self.confidence.value,
            "reasons": self.reasons,
        }


def assess_competition(
    history: Sequence[CompetitionPoint],
    offers: Sequence[OfferSnapshot] = (),
    *,
    now: datetime | None = None,
) -> CompetitionAssessment:
    now = now or utcnow()
    ordered = sorted(history, key=lambda point: ensure_utc(point.observed_at))
    recent = [
        point for point in ordered if ensure_utc(point.observed_at) >= now - timedelta(days=30)
    ]
    reasons: list[str] = []

    latest = ordered[-1] if ordered else None
    seller_count = latest.seller_count if latest else None
    offer_count = latest.offer_count if latest else None
    if offers and seller_count is None:
        seller_count = len({offer.seller_id for offer in offers if offer.seller_id})
        offer_count = len(offers)

    counts = [point.seller_count for point in recent if point.seller_count is not None]
    average_30d = ratio(Decimal(sum(counts)) / Decimal(len(counts))) if counts else None

    trend = TrendDirection.UNKNOWN
    change_pct: Decimal | None = None
    if len(counts) >= MIN_POINTS_FOR_TREND:
        half = max(1, len(counts) // 2)
        earlier = Decimal(sum(counts[:half])) / Decimal(half)
        later = Decimal(sum(counts[half:])) / Decimal(len(counts) - half or 1)
        if earlier > 0:
            change_pct = ratio((later - earlier) / earlier)
            if abs(change_pct) < Decimal("0.1"):
                trend = TrendDirection.FLAT
            else:
                trend = TrendDirection.RISING if change_pct > 0 else TrendDirection.FALLING

    # Entries and exits from set differences rather than count deltas: three in
    # and three out is a churning listing, and a net change of zero hides it.
    entrants = exits = None
    with_ids = [point for point in recent if point.seller_ids]
    if len(with_ids) >= 2:
        first_ids = set(with_ids[0].seller_ids)
        last_ids = set(with_ids[-1].seller_ids)
        entrants = len(last_ids - first_ids)
        exits = len(first_ids - last_ids)

    landed = sorted(offer.landed for offer in offers) if offers else []
    lowest = money(landed[0]) if landed else (latest.lowest_price if latest else None)
    highest = money(landed[-1]) if landed else None
    median_offer = (
        money(statistics.median(landed)) if landed else (latest.median_price if latest else None)
    )
    buy_box = next((offer.landed for offer in offers if offer.is_buy_box), None)
    if buy_box is None and latest is not None:
        buy_box = latest.buy_box_price

    dispersion = None
    if lowest is not None and highest is not None and median_offer and median_offer > 0:
        dispersion = ratio((highest - lowest) / median_offer)

    marketplace_is_seller = bool(
        any(offer.is_marketplace_seller for offer in offers)
        or (latest.marketplace_is_seller if latest else False)
    )

    # -- pressure score ----------------------------------------------------
    pressure = Decimal("0")
    if seller_count is not None:
        if seller_count <= 2:
            pressure += Decimal("5")
        elif seller_count <= 5:
            pressure += Decimal("20")
        elif seller_count <= CROWDED_SELLER_COUNT:
            pressure += Decimal("40")
        elif seller_count <= 20:
            pressure += Decimal("60")
        else:
            pressure += Decimal("75")
        reasons.append(f"{seller_count} seller(s) on the listing.")
    else:
        pressure += Decimal("35")
        reasons.append("Seller count unknown; competition is assumed moderate.")

    if change_pct is not None and change_pct >= SELLER_GROWTH_THRESHOLD:
        pressure += Decimal("20")
        reasons.append(
            f"Seller count is up {change_pct:.0%} over the last 30 days "
            f"(30-day average {average_30d})."
        )
    elif trend is TrendDirection.FALLING and change_pct is not None:
        pressure -= Decimal("10")
        reasons.append(f"Seller count is down {abs(change_pct):.0%} over the last 30 days.")

    if entrants is not None and entrants >= 3:
        pressure += Decimal("10")
        reasons.append(f"{entrants} new seller(s) entered in the last 30 days.")

    if marketplace_is_seller:
        pressure += Decimal("15")
        reasons.append(
            "The marketplace itself is selling this listing, which usually means "
            "the buy box is unwinnable at a profitable price."
        )

    if dispersion is not None and dispersion >= Decimal("0.25"):
        pressure += Decimal("5")
        reasons.append(
            f"Offer prices are dispersed by {dispersion:.0%} of the median: "
            "the headline price is not what a new seller would realise."
        )

    pressure = max(Decimal("0"), min(Decimal("100"), pressure))

    if pressure >= 75:
        level = RiskLevel.CRITICAL
    elif pressure >= 55:
        level = RiskLevel.HIGH
    elif pressure >= 30:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    if len(counts) >= 10:
        confidence = Confidence.HIGH
    elif len(counts) >= MIN_POINTS_FOR_TREND or offers:
        confidence = Confidence.MEDIUM
    elif seller_count is not None:
        confidence = Confidence.LOW
    else:
        confidence = Confidence.NONE

    return CompetitionAssessment(
        seller_count=seller_count,
        offer_count=offer_count,
        average_seller_count_30d=average_30d,
        seller_trend=trend,
        seller_change_pct=change_pct,
        entrants_30d=entrants,
        exits_30d=exits,
        buy_box_price=money(buy_box) if buy_box is not None else None,
        lowest_offer=lowest,
        median_offer=median_offer,
        highest_offer=highest,
        price_dispersion=dispersion,
        marketplace_is_seller=marketplace_is_seller,
        risk_level=level,
        pressure_score=pressure,
        confidence=confidence,
        reasons=reasons,
    )
