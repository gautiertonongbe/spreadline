"""How long capital actually stays tied up.

Every return figure in this product is a return on capital with no time in it. A
20% return in ten days and a 30% return in ninety are not comparable, and until
now nothing here could tell them apart: the allocator ranked on profit per
dollar and said so, because the platform could not measure the other half.

This measures it, from the only evidence that can: positions that actually
closed. Days from opening to the last unit sold, per position, then aggregated.

Three rules keep it honest, and the third is the one that matters:

**Only closed positions count.** An open position has no holding period yet, and
counting today's age as if it were the final one drags every median towards
whatever is currently unsold — which is exactly the slow-moving inventory the
measurement exists to warn about.

**A segment has to earn its own number.** Below ``MIN_SAMPLE`` closed positions a
segment gets no median of its own. It falls back to the portfolio-wide figure,
which is still measured rather than assumed, and the fallback is reported so a
reader knows whether a number describes *this kind of product* or the portfolio
in general.

**No history, no number.** With nothing closed there is no median, no fallback
and no estimate. The result says "not measured yet" and every consumer is
expected to carry on without it, because a default holding period invented to
fill the gap would become the most important number in the ranking while being
the only one nobody measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc
from app.core.money import money, pct_of
from app.core.security import AuthContext
from app.models.autonomy import CapitalPosition
from app.models.enums import ExecutionMode, PositionStatus

#: Closed positions a segment needs before it gets a holding period of its own.
#: The same bar the learning report uses for naming a pattern, for the same
#: reason: below it the number is a story about two or three sales.
MIN_SAMPLE = 5

#: Days in a year, for annualising. 365 rather than a trading calendar: this is
#: inventory, and it sits on a shelf at weekends too.
DAYS_IN_YEAR = Decimal("365")

#: A position that closed the day it opened still occupied capital. Counting it
#: as zero days would make its annualised return infinite, so the floor is one.
MINIMUM_DAYS = 1


@dataclass
class Observation:
    """One closed position, reduced to what the measurement needs."""

    position_id: str
    days_held: int
    capital: Decimal
    realized_profit: Decimal
    brand: str | None
    category: str | None
    marketplace: str | None

    @property
    def realized_roi(self) -> Decimal | None:
        return pct_of(self.realized_profit, self.capital)

    @property
    def annualized_roi(self) -> Decimal | None:
        """Return scaled to a year at this position's pace.

        A comparison device, not a forecast: it says what this capital would
        earn if it kept turning over at the speed it just did, which nothing
        guarantees it will.
        """
        roi = self.realized_roi
        if roi is None:
            return None
        return (roi * DAYS_IN_YEAR / Decimal(max(MINIMUM_DAYS, self.days_held))).quantize(
            Decimal("0.0001")
        )


@dataclass
class SegmentVelocity:
    """The measured holding period for one segment, with its sample."""

    dimension: str
    segment: str
    sample: int
    median_days: int
    fastest_days: int
    slowest_days: int
    median_roi: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "segment": self.segment,
            "sample": self.sample,
            "median_days": self.median_days,
            "fastest_days": self.fastest_days,
            "slowest_days": self.slowest_days,
            "median_roi": None if self.median_roi is None else str(self.median_roi),
        }


@dataclass
class VelocityReport:
    """What the platform can say about how fast capital comes back."""

    observations: list[Observation] = field(default_factory=list)
    segments: list[SegmentVelocity] = field(default_factory=list)
    #: Segments looked at and refused a number, so the absence is visible.
    insufficient: list[dict[str, Any]] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return bool(self.observations)

    @property
    def sample(self) -> int:
        return len(self.observations)

    @property
    def portfolio_median_days(self) -> int | None:
        """The fallback. Measured, not assumed, and absent when nothing closed."""
        if not self.observations:
            return None
        return _median_int([item.days_held for item in self.observations])

    @property
    def median_annualized_roi(self) -> Decimal | None:
        values = [
            item.annualized_roi for item in self.observations if item.annualized_roi is not None
        ]
        return _median_decimal(values) if values else None

    def expected_days(self, *, category: str | None, brand: str | None) -> tuple[int, str] | None:
        """How long a position in this segment has historically taken.

        Returns the figure and where it came from, or None when there is no
        measured history at all. Category before brand: what a thing *is* moves
        sell-through more than who made it, and a brand spans categories that
        behave nothing alike.
        """
        for dimension, key in (("category", category), ("brand", brand)):
            if not key:
                continue
            for entry in self.segments:
                if entry.dimension == dimension and entry.segment == key:
                    return entry.median_days, f"{entry.sample} closed in {dimension} '{key}'"
        portfolio = self.portfolio_median_days
        if portfolio is None:
            return None
        return portfolio, f"{self.sample} closed across the portfolio"

    @property
    def summary(self) -> str:
        if not self.measured:
            return (
                "Holding period is not measured yet: no position has closed. Until one "
                "does, a return figure here carries no time in it, and nothing invents a "
                "holding period to fill the gap."
            )
        median = self.portfolio_median_days
        parts = [
            f"{self.sample} closed position(s). Capital comes back in {median} day(s) at "
            "the median"
        ]
        annual = self.median_annualized_roi
        if annual is not None:
            parts.append(f", which is {annual:.0%} a year at that pace.")
        else:
            parts.append(".")
        if self.segments:
            parts.append(
                f" {len(self.segments)} segment(s) have enough closed positions to be "
                "measured on their own."
            )
        return "".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "measured": self.measured,
            "summary": self.summary,
            "sample": self.sample,
            "minimum_sample": MIN_SAMPLE,
            "portfolio_median_days": self.portfolio_median_days,
            "median_annualized_roi": (
                None if self.median_annualized_roi is None else str(self.median_annualized_roi)
            ),
            "segments": [entry.as_dict() for entry in self.segments],
            "insufficient": self.insufficient,
            "note": (
                "Measured from closed positions only. An open position has no holding "
                "period yet, and counting its current age would drag every figure towards "
                "whatever has not sold."
            ),
        }


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _median_decimal(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return money((ordered[middle - 1] + ordered[middle]) / 2)


def measure(
    session: Session,
    auth: AuthContext,
    *,
    execution_mode: ExecutionMode | None = None,
) -> VelocityReport:
    """Everything the closed positions can say about holding period."""
    query = select(CapitalPosition).where(
        CapitalPosition.organization_id == auth.organization_id,
        CapitalPosition.status == PositionStatus.CLOSED.value,
        CapitalPosition.closed_at.is_not(None),
    )
    if execution_mode is not None:
        query = query.where(CapitalPosition.execution_mode == execution_mode.value)

    observations: list[Observation] = []
    for row in session.scalars(query):
        days = (ensure_utc(row.closed_at) - ensure_utc(row.opened_at)).days
        observations.append(
            Observation(
                position_id=row.id,
                days_held=max(MINIMUM_DAYS, days),
                capital=money(row.capital_invested),
                realized_profit=money(row.realized_profit),
                brand=row.brand,
                category=row.category,
                marketplace=row.target_marketplace,
            )
        )

    report = VelocityReport(observations=observations)
    if not observations:
        return report

    for dimension, key in (
        ("category", lambda item: item.category),
        ("brand", lambda item: item.brand),
        ("marketplace", lambda item: item.marketplace),
    ):
        buckets: dict[str, list[Observation]] = {}
        for item in observations:
            value = key(item)
            if value:
                buckets.setdefault(value, []).append(item)

        for segment, rows in buckets.items():
            if len(rows) < MIN_SAMPLE:
                report.insufficient.append(
                    {
                        "dimension": dimension,
                        "segment": segment,
                        "sample": len(rows),
                        "reason": (
                            f"{len(rows)} closed position(s); {MIN_SAMPLE} are needed before "
                            "this segment gets a holding period of its own."
                        ),
                    }
                )
                continue
            days = [item.days_held for item in rows]
            rois = [item.realized_roi for item in rows if item.realized_roi is not None]
            report.segments.append(
                SegmentVelocity(
                    dimension=dimension,
                    segment=segment,
                    sample=len(rows),
                    median_days=_median_int(days),
                    fastest_days=min(days),
                    slowest_days=max(days),
                    median_roi=_median_decimal(rois) if rois else None,
                )
            )

    # Slowest first: the segment tying capital up longest is the one worth
    # seeing, and it is the one a return-only ranking would keep buying.
    report.segments.sort(key=lambda entry: (-entry.median_days, entry.segment))
    return report
