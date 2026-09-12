"""Demand intelligence.

The hardest rule in the platform lives here: never fabricate demand.

Sales rank is an ordinal position, not a quantity. Turning a rank into units sold
requires a per-marketplace, per-category calibration that Spreadline does not
have and will not invent. So this module reports a *relative* demand score, says
plainly what it is based on, and returns ``estimated_monthly_units = None`` with a
reason unless a provider supplied an estimate and named its basis (spec §12).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from math import log10
from typing import Any

from app.core.clock import ensure_utc, utcnow
from app.core.money import display_score, ratio
from app.models.enums import Confidence, TrendDirection

#: Rank at which the relative score reaches zero. Ranks beyond a million are
#: functionally indistinguishable from "does not sell".
RANK_FLOOR = 1_000_000
#: Minimum spread of observations before a velocity or trend is reported.
MIN_DAYS_FOR_VELOCITY = 14
MIN_POINTS_FOR_TREND = 5


@dataclass(frozen=True)
class DemandPoint:
    sales_rank: int | None
    observed_at: datetime
    review_count: int | None = None
    rank_category: str | None = None
    estimated_monthly_units: int | None = None
    estimation_basis: str | None = None


@dataclass
class DemandAssessment:
    #: 0..100 relative demand. None when there is nothing to base it on.
    score: Decimal | None
    confidence: Confidence
    current_rank: int | None = None
    median_rank: int | None = None
    best_rank: int | None = None
    rank_category: str | None = None
    rank_trend: TrendDirection = TrendDirection.UNKNOWN
    #: New reviews per 30 days. A proxy for velocity, not a sales figure.
    review_velocity_30d: Decimal | None = None
    current_review_count: int | None = None
    estimated_monthly_units: int | None = None
    estimation_basis: str | None = None
    observation_count: int = 0
    span_days: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return self.confidence is not Confidence.NONE

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": None if self.score is None else str(self.score),
            "confidence": self.confidence.value,
            "current_rank": self.current_rank,
            "median_rank": self.median_rank,
            "best_rank": self.best_rank,
            "rank_category": self.rank_category,
            "rank_trend": self.rank_trend.value,
            "review_velocity_30d": (
                None if self.review_velocity_30d is None else str(self.review_velocity_30d)
            ),
            "current_review_count": self.current_review_count,
            "estimated_monthly_units": self.estimated_monthly_units,
            "estimation_basis": self.estimation_basis,
            "observation_count": self.observation_count,
            "span_days": self.span_days,
            "reasons": self.reasons,
        }


def rank_to_relative_score(rank: int | None) -> Decimal | None:
    """Map a sales rank to a 0..100 relative score on a log scale.

    Log scale because rank 100 versus rank 1,000 is a far larger difference in
    real demand than rank 100,000 versus rank 101,000, and a linear transform
    would treat them as identical.

    This is explicitly not an estimate of units. It orders products by how well
    they sell relative to their marketplace, nothing more.
    """
    if rank is None or rank <= 0:
        return None
    if rank >= RANK_FLOOR:
        return Decimal("0")
    span = log10(RANK_FLOOR)
    score = (1 - (log10(rank) / span)) * 100
    return ratio(Decimal(str(round(max(0.0, min(100.0, score)), 2))))


def _rank_trend(points: Sequence[DemandPoint]) -> TrendDirection:
    """Rank trend. Note the inversion: a falling rank number is rising demand."""
    ranked = [point for point in points if point.sales_rank is not None]
    if len(ranked) < MIN_POINTS_FOR_TREND:
        return TrendDirection.UNKNOWN
    ordered = sorted(ranked, key=lambda point: ensure_utc(point.observed_at))
    half = len(ordered) // 2
    earlier = statistics.median([point.sales_rank for point in ordered[:half]])  # type: ignore[misc]
    later = statistics.median([point.sales_rank for point in ordered[half:]])  # type: ignore[misc]
    if earlier <= 0:
        return TrendDirection.UNKNOWN
    change = (later - earlier) / earlier
    if abs(change) < 0.1:
        return TrendDirection.FLAT
    return TrendDirection.RISING if change < 0 else TrendDirection.FALLING


def assess_demand(
    points: Sequence[DemandPoint],
    *,
    current_rank: int | None = None,
    current_review_count: int | None = None,
    now: datetime | None = None,
) -> DemandAssessment:
    now = now or utcnow()
    ordered = sorted(points, key=lambda point: ensure_utc(point.observed_at))
    ranked = [point for point in ordered if point.sales_rank is not None]

    if not ordered and current_rank is None:
        return DemandAssessment(
            score=None,
            confidence=Confidence.NONE,
            reasons=[
                "No demand observations are available for this listing. "
                "Demand is unknown, which is not the same as demand being low."
            ],
        )

    span_days = 0
    if len(ordered) >= 2:
        span_days = (ensure_utc(ordered[-1].observed_at) - ensure_utc(ordered[0].observed_at)).days

    latest_rank = (
        current_rank if current_rank is not None else (ranked[-1].sales_rank if ranked else None)
    )
    reasons: list[str] = []

    if latest_rank is None:
        reasons.append(
            "No sales rank on any observation; demand cannot be scored from the data held."
        )
        return DemandAssessment(
            score=None,
            confidence=Confidence.NONE,
            observation_count=len(ordered),
            span_days=span_days,
            current_review_count=current_review_count,
            reasons=reasons,
        )

    rank_values = [point.sales_rank for point in ranked if point.sales_rank is not None]
    median_rank = int(statistics.median(rank_values)) if rank_values else latest_rank
    best_rank = min(rank_values) if rank_values else latest_rank

    # The score is based on the median rank, not the latest, so one good day does
    # not make a product look like a mover.
    score = rank_to_relative_score(median_rank)
    trend = _rank_trend(ordered)

    velocity: Decimal | None = None
    review_points = [point for point in ordered if point.review_count is not None]
    if len(review_points) >= 2:
        first, last = review_points[0], review_points[-1]
        elapsed = (ensure_utc(last.observed_at) - ensure_utc(first.observed_at)).days
        if (
            elapsed >= MIN_DAYS_FOR_VELOCITY
            and last.review_count is not None
            and first.review_count is not None
        ):
            delta = last.review_count - first.review_count
            velocity = ratio(Decimal(delta) * Decimal("30") / Decimal(elapsed))
        else:
            reasons.append(
                f"Review velocity needs {MIN_DAYS_FOR_VELOCITY} days of history; "
                f"only {elapsed} day(s) are available."
            )

    # A provider estimate is passed through only with its basis attached.
    estimate, basis = None, None
    for point in reversed(ordered):
        if point.estimated_monthly_units is not None and point.estimation_basis:
            estimate, basis = point.estimated_monthly_units, point.estimation_basis
            break
    if estimate is None:
        reasons.append(
            "No unit-sales estimate: converting a sales rank to units requires a "
            "category calibration that is not available, so none is shown."
        )

    if len(ranked) >= 10 and span_days >= 30:
        confidence = Confidence.HIGH
    elif len(ranked) >= MIN_POINTS_FOR_TREND and span_days >= 7:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
        reasons.append(
            f"Demand confidence is low: {len(ranked)} rank observation(s) over {span_days} day(s)."
        )

    reasons.insert(
        0,
        f"Relative demand score {display_score(score)} derived from a median rank of "
        f"{median_rank:,}"
        + (f" in {ranked[-1].rank_category}." if ranked and ranked[-1].rank_category else "."),
    )

    return DemandAssessment(
        score=score,
        confidence=confidence,
        current_rank=latest_rank,
        median_rank=median_rank,
        best_rank=best_rank,
        rank_category=next(
            (point.rank_category for point in reversed(ordered) if point.rank_category), None
        ),
        rank_trend=trend,
        review_velocity_30d=velocity,
        current_review_count=(
            current_review_count
            if current_review_count is not None
            else next(
                (point.review_count for point in reversed(ordered) if point.review_count), None
            )
        ),
        estimated_monthly_units=estimate,
        estimation_basis=basis,
        observation_count=len(ordered),
        span_days=span_days,
        reasons=reasons,
    )
