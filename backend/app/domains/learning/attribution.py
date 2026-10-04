"""Where the predictions go wrong, and whether it is systematic.

The scorecard says how accurate the system has been. It cannot say *where* it is
wrong, and an average error is the one number guaranteed to hide a pattern: a
category consistently overestimated by 20% and another underestimated by 20%
average to nothing at all.

This module segments closed outcomes and looks for bias. It is the first half of
the learning loop:

    decision -> outcome -> error -> analysis -> rule improvement

Deliberately the first half only. Nothing here changes a fee assumption, a
weight or a threshold. It produces a finding a person can act on, because an
engine that silently retunes itself on twelve observations is how a small
sampling accident becomes a permanent rule.

Two properties keep a finding honest:

**Magnitude and consistency are both required.** A mean error of zero across
+50% and -50% is not accuracy, and a mean error of 20% across two outcomes is
not a pattern. A finding needs enough observations, a large enough average
error, and most of the errors pointing the same way.

**Direction is stated, not implied.** "Overestimated" and "underestimated" are
different problems with different fixes: the first inflates what the platform
will buy, the second makes it walk past money.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import display_currency, money, ratio
from app.core.security import AuthContext
from app.models.catalog import Product
from app.models.opportunity import Opportunity
from app.models.portfolio import Outcome

#: Below this many closed outcomes in a segment, no finding is reported. A
#: pattern claimed from three sales is a story, not a measurement.
MIN_SAMPLE = 5
#: Average signed error, as a share of predicted profit, before a segment is
#: worth reporting at all.
MIN_BIAS = Decimal("0.15")
#: Share of the outcomes in a segment that must err in the same direction. Two
#: large errors cancelling out is not a bias, it is noise.
MIN_AGREEMENT = Decimal("0.70")


@dataclass
class Observation:
    """One closed outcome, reduced to what attribution needs."""

    outcome_id: str
    predicted: Decimal
    actual: Decimal
    capital: Decimal
    days_to_sell: int | None
    category: str | None
    brand: str | None
    marketplace: str | None
    risk_level: str | None

    @property
    def signed_error(self) -> Decimal | None:
        """(actual - predicted) / predicted. Negative means overestimated.

        ``None`` when the prediction was zero: a percentage error against a zero
        prediction is undefined, and treating it as a large error would let one
        break-even trade dominate a segment.
        """
        if self.predicted == 0:
            return None
        return ratio((self.actual - self.predicted) / abs(self.predicted))


@dataclass
class Finding:
    """One segment where the predictions are consistently off."""

    dimension: str
    segment: str
    sample: int
    #: Negative means the platform overestimated profit in this segment.
    mean_signed_error: Decimal
    mean_absolute_error: Decimal
    agreement: Decimal
    capital_involved: Decimal
    predicted_total: Decimal
    actual_total: Decimal

    @property
    def direction(self) -> str:
        return "overestimated" if self.mean_signed_error < 0 else "underestimated"

    @property
    def impact(self) -> Decimal:
        """How much money the bias has accounted for so far.

        Findings are ranked by this rather than by error size: a 40% error on
        one $12 position matters less than a 16% error on eight $300 ones.
        """
        return money(abs(self.actual_total - self.predicted_total))

    @property
    def summary(self) -> str:
        return (
            f"{self.dimension.replace('_', ' ').title()} '{self.segment}': profit has been "
            f"{self.direction} by {abs(self.mean_signed_error):.0%} on average across "
            f"{self.sample} closed position(s), {self.agreement:.0%} of them in the same "
            f"direction. Predicted {display_currency(self.predicted_total)} in total, "
            f"actually {display_currency(self.actual_total)}."
        )

    @property
    def suggestion(self) -> str:
        """What a person might do about it. A prompt, never an action."""
        if self.direction == "overestimated":
            return (
                "The exit price or the fees for this segment are optimistic. Worth "
                "checking the fee assumptions and whether the expected sale price "
                "holds up, before raising the minimum return for it."
            )
        return (
            "Returns here have been better than predicted, so the platform may be "
            "walking past workable candidates in this segment. Worth checking what "
            "the model is penalising."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "segment": self.segment,
            "sample": self.sample,
            "direction": self.direction,
            "mean_signed_error": str(self.mean_signed_error),
            "mean_absolute_error": str(self.mean_absolute_error),
            "agreement": str(self.agreement),
            "capital_involved": str(self.capital_involved),
            "predicted_total": str(self.predicted_total),
            "actual_total": str(self.actual_total),
            "impact": str(self.impact),
            "summary": self.summary,
            "suggestion": self.suggestion,
        }


@dataclass
class AttributionReport:
    observations: int
    findings: list[Finding] = field(default_factory=list)
    #: Segments that were looked at but had too little behind them to judge.
    insufficient: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.observations == 0:
            return (
                "No closed positions yet. Prediction error can only be attributed once "
                "predictions have been compared against what actually happened."
            )
        if not self.findings:
            return (
                f"No systematic bias found across {self.observations} closed position(s). "
                "That is not the same as the predictions being good: it means no segment "
                "has enough consistent error behind it to call a pattern."
            )
        leading = self.findings[0]
        return (
            f"{len(self.findings)} segment(s) show consistent bias across "
            f"{self.observations} closed position(s). Largest by money: {leading.summary}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "summary": self.summary,
            "minimum_sample": MIN_SAMPLE,
            "findings": [finding.as_dict() for finding in self.findings],
            "insufficient": self.insufficient,
        }


def _price_band(capital: Decimal, quantity: int) -> str:
    """Unit price bucket. Error often tracks price level rather than product."""
    unit = capital / quantity if quantity else capital
    if unit < 25:
        return "under $25"
    if unit < 100:
        return "$25 to $100"
    if unit < 250:
        return "$100 to $250"
    return "$250 and above"


def _load(session: Session, auth: AuthContext) -> list[Observation]:
    """Closed outcomes with both sides of the comparison present."""
    rows = list(
        session.scalars(
            select(Outcome).where(
                Outcome.organization_id == auth.organization_id,
                Outcome.is_closed.is_(True),
            )
        )
    )
    if not rows:
        return []

    product_ids = {row.product_id for row in rows if row.product_id}
    products = {
        product.id: product
        for product in session.scalars(select(Product).where(Product.id.in_(product_ids)))
    }
    opportunity_ids = {row.opportunity_id for row in rows if row.opportunity_id}
    opportunities = (
        {
            opportunity.id: opportunity
            for opportunity in session.scalars(
                select(Opportunity).where(Opportunity.id.in_(opportunity_ids))
            )
        }
        if opportunity_ids
        else {}
    )

    observations: list[Observation] = []
    for row in rows:
        if row.predicted_total_profit is None or row.actual_profit is None:
            continue
        product = products.get(row.product_id or "")
        opportunity = opportunities.get(row.opportunity_id or "")
        observations.append(
            Observation(
                outcome_id=row.id,
                predicted=money(row.predicted_total_profit),
                actual=money(row.actual_profit),
                capital=money(row.capital_deployed),
                days_to_sell=row.days_to_sell,
                category=product.category if product else None,
                brand=product.brand if product else None,
                marketplace=opportunity.target_marketplace if opportunity else None,
                risk_level=opportunity.risk_level if opportunity else None,
            )
        )
    return observations


#: The dimensions worth segmenting on, and how to read each from an observation.
DIMENSIONS: dict[str, Callable[[Observation], str | None]] = {
    "category": lambda item: item.category,
    "brand": lambda item: item.brand,
    "marketplace": lambda item: item.marketplace,
    "risk_level": lambda item: item.risk_level,
    "price_band": lambda item: _price_band(item.capital, 1),
}


def _segment_finding(
    dimension: str, segment: str, items: list[Observation]
) -> tuple[Finding | None, dict[str, Any] | None]:
    """Judge one segment. Returns a finding, or a note on why there is none."""
    errors = [item.signed_error for item in items]
    usable = [error for error in errors if error is not None]

    if len(usable) < MIN_SAMPLE:
        return None, {
            "dimension": dimension,
            "segment": segment,
            "sample": len(usable),
            "reason": (
                f"{len(usable)} closed position(s); {MIN_SAMPLE} are needed before a "
                "pattern can be called."
            ),
        }

    mean_signed = ratio(sum(usable) / Decimal(len(usable)))
    mean_absolute = ratio(sum(abs(error) for error in usable) / Decimal(len(usable)))

    # Consistency: how much of the segment errs the same way as the average.
    same_way = sum(
        1 for error in usable if (error < 0) == (mean_signed < 0) and error != 0
    )
    agreement = ratio(Decimal(same_way) / Decimal(len(usable)))

    if abs(mean_signed) < MIN_BIAS or agreement < MIN_AGREEMENT:
        return None, {
            "dimension": dimension,
            "segment": segment,
            "sample": len(usable),
            "reason": (
                f"Average error {mean_signed:.0%} with {agreement:.0%} agreement: not "
                f"consistent enough to call a bias (needs {MIN_BIAS:.0%} and "
                f"{MIN_AGREEMENT:.0%})."
            ),
        }

    return (
        Finding(
            dimension=dimension,
            segment=segment,
            sample=len(usable),
            mean_signed_error=mean_signed,
            mean_absolute_error=mean_absolute,
            agreement=agreement,
            capital_involved=money(sum((item.capital for item in items), Decimal("0"))),
            predicted_total=money(sum((item.predicted for item in items), Decimal("0"))),
            actual_total=money(sum((item.actual for item in items), Decimal("0"))),
        ),
        None,
    )


def attribute(session: Session, auth: AuthContext) -> AttributionReport:
    """Find the segments where the predictions are consistently wrong."""
    observations = _load(session, auth)
    if not observations:
        return AttributionReport(observations=0)

    findings: list[Finding] = []
    insufficient: list[dict[str, Any]] = []

    for dimension, read in DIMENSIONS.items():
        buckets: dict[str, list[Observation]] = {}
        for item in observations:
            key = read(item)
            if not key:
                continue
            buckets.setdefault(key, []).append(item)

        for segment, items in sorted(buckets.items()):
            finding, note = _segment_finding(dimension, segment, items)
            if finding is not None:
                findings.append(finding)
            elif note is not None:
                insufficient.append(note)

    # Ranked by money rather than by error size: a 40% error on one small
    # position matters less than a 16% error on eight large ones.
    findings.sort(key=lambda finding: finding.impact, reverse=True)
    return AttributionReport(
        observations=len(observations),
        findings=findings,
        insufficient=insufficient,
    )
