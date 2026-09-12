"""Data quality scoring.

Data quality is a first-class concept, not a footnote (spec §31). Every analysis
carries a score built from named dimensions, each with its own confidence and the
reason it is what it is.

The central rule: missing data reduces the score. It is never filled in with an
assumption and then scored as though it were observed. A product with no demand
history scores lower than one with demand history, even when every other number
looks better, because the platform genuinely knows less about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.clock import age_seconds, utcnow
from app.core.config import settings
from app.core.money import ratio
from app.models.enums import Confidence, DataQualityDimension

#: Weight of each dimension in the overall score. Sums to 1.
DIMENSION_WEIGHTS: dict[DataQualityDimension, Decimal] = {
    DataQualityDimension.PRICE: Decimal("0.25"),
    DataQualityDimension.PRODUCT_MATCH: Decimal("0.25"),
    DataQualityDimension.HISTORICAL_DATA: Decimal("0.20"),
    DataQualityDimension.COMPETITION: Decimal("0.15"),
    DataQualityDimension.DEMAND: Decimal("0.10"),
    DataQualityDimension.AVAILABILITY: Decimal("0.05"),
}

CONFIDENCE_SCORE: dict[Confidence, Decimal] = {
    Confidence.HIGH: Decimal("100"),
    Confidence.MEDIUM: Decimal("70"),
    Confidence.LOW: Decimal("40"),
    Confidence.NONE: Decimal("0"),
}

#: TTL per data type, from configuration. Data older than its TTL is stale and
#: the dimension is downgraded rather than silently trusted.
TTL_BY_DIMENSION: dict[DataQualityDimension, int] = {
    DataQualityDimension.PRICE: settings.ttl_current_price_seconds,
    DataQualityDimension.AVAILABILITY: settings.ttl_availability_seconds,
    DataQualityDimension.COMPETITION: settings.ttl_competition_seconds,
    DataQualityDimension.DEMAND: settings.ttl_demand_seconds,
    DataQualityDimension.HISTORICAL_DATA: settings.ttl_price_history_seconds,
    DataQualityDimension.PRODUCT_MATCH: settings.ttl_product_metadata_seconds,
}


@dataclass
class QualityDimension:
    dimension: DataQualityDimension
    confidence: Confidence
    score: Decimal
    reason: str
    observed_at: datetime | None = None
    is_stale: bool = False
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "confidence": self.confidence.value,
            "score": str(self.score),
            "reason": self.reason,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "is_stale": self.is_stale,
            "source": self.source,
        }


@dataclass
class DataQualityScore:
    #: 0..100.
    score: Decimal
    confidence: Confidence
    dimensions: list[QualityDimension] = field(default_factory=list)

    def dimension(self, dimension: DataQualityDimension) -> QualityDimension | None:
        return next((item for item in self.dimensions if item.dimension is dimension), None)

    @property
    def missing_dimensions(self) -> list[DataQualityDimension]:
        return [item.dimension for item in self.dimensions if item.confidence is Confidence.NONE]

    @property
    def stale_dimensions(self) -> list[DataQualityDimension]:
        return [item.dimension for item in self.dimensions if item.is_stale]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": str(self.score),
            "confidence": self.confidence.value,
            "dimensions": [item.as_dict() for item in self.dimensions],
            "missing": [item.value for item in self.missing_dimensions],
            "stale": [item.value for item in self.stale_dimensions],
        }


def _staleness(
    dimension: DataQualityDimension, observed_at: datetime | None, now: datetime
) -> tuple[bool, Decimal]:
    """(is_stale, multiplier). Stale data keeps some value, but not all of it."""
    ttl = TTL_BY_DIMENSION.get(dimension)
    if observed_at is None or ttl is None:
        return False, Decimal("1")
    age = age_seconds(observed_at, now=now)
    if age <= ttl:
        return False, Decimal("1")
    # Two TTLs old is worth half; beyond that, a quarter. Old data is still
    # evidence, just weaker evidence.
    return True, Decimal("0.5") if age <= ttl * 2 else Decimal("0.25")


def build_dimension(
    dimension: DataQualityDimension,
    confidence: Confidence,
    reason: str,
    *,
    observed_at: datetime | None = None,
    source: str | None = None,
    now: datetime | None = None,
) -> QualityDimension:
    now = now or utcnow()
    is_stale, multiplier = _staleness(dimension, observed_at, now)
    score = ratio(CONFIDENCE_SCORE[confidence] * multiplier)
    if is_stale:
        reason = f"{reason.rstrip('.')} (observation is stale)."
    return QualityDimension(
        dimension=dimension,
        confidence=confidence,
        score=score,
        reason=reason,
        observed_at=observed_at,
        is_stale=is_stale,
        source=source,
    )


def score_quality(dimensions: list[QualityDimension]) -> DataQualityScore:
    """Weighted mean over the dimensions supplied.

    Dimensions that were not supplied at all are treated as absent evidence and
    scored zero, so an analysis that simply skipped competition cannot come out
    looking as clean as one that measured it.
    """
    if not dimensions:
        return DataQualityScore(score=Decimal("0"), confidence=Confidence.NONE)

    supplied = {item.dimension: item for item in dimensions}
    total = Decimal("0")
    for dimension, weight in DIMENSION_WEIGHTS.items():
        item = supplied.get(dimension)
        total += weight * (item.score if item else Decimal("0"))

    score = ratio(total)
    if score >= 80:
        confidence = Confidence.HIGH
    elif score >= 55:
        confidence = Confidence.MEDIUM
    elif score > 0:
        confidence = Confidence.LOW
    else:
        confidence = Confidence.NONE

    # An unsupplied dimension is still reported, so the gap is visible.
    complete = list(dimensions)
    for dimension in DIMENSION_WEIGHTS:
        if dimension not in supplied:
            complete.append(
                QualityDimension(
                    dimension=dimension,
                    confidence=Confidence.NONE,
                    score=Decimal("0"),
                    reason="Not measured in this analysis.",
                )
            )
    return DataQualityScore(score=score, confidence=confidence, dimensions=complete)
