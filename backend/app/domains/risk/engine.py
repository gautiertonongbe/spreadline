"""The risk engine.

Profitability and risk are different questions and Spreadline keeps them apart
(spec §15). A 60% ROI on a product whose price halved last week, sold by nine new
sellers this month, matched on title similarity alone, is not a good opportunity.
The profitability engine is right and the trade is still bad.

Every signal carries a code, a severity, a weight, a message and its evidence, so
a risk level always explains itself rather than asserting a colour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import display, display_score, ratio
from app.domains.opportunities.context import AnalysisContext
from app.models.enums import (
    AnomalyType,
    Availability,
    Confidence,
    MatchStatus,
    RiskLevel,
    Severity,
    TrendDirection,
)

RISK_MODEL_VERSION = "risk-v1"

#: Contribution of a signal at each severity, before its own weight.
SEVERITY_POINTS: dict[Severity, Decimal] = {
    Severity.INFO: Decimal("0"),
    Severity.LOW: Decimal("8"),
    Severity.MEDIUM: Decimal("20"),
    Severity.HIGH: Decimal("35"),
    Severity.CRITICAL: Decimal("60"),
}

#: Categories and brands that are commonly gated, counterfeited or returned at
#: high rates. Configurable; the default list is a starting point.
ELEVATED_RISK_CATEGORIES = {
    "clothing",
    "shoes",
    "beauty",
    "health & personal care",
    "grocery",
    "baby",
    "jewelry",
    "watches",
    "cell phone accessories",
}
ELEVATED_RISK_BRANDS = {
    "nike",
    "adidas",
    "apple",
    "disney",
    "lego",
    "sony",
    "samsung",
    "lululemon",
    "north face",
    "under armour",
}


@dataclass
class RiskSignal:
    code: str
    severity: Severity
    weight: Decimal
    message: str
    evidence: list[str] = field(default_factory=list)
    #: A blocking signal forces a PASS regardless of the numbers.
    is_blocking: bool = False

    @property
    def points(self) -> Decimal:
        return ratio(SEVERITY_POINTS[self.severity] * self.weight)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "weight": str(self.weight),
            "points": str(self.points),
            "message": self.message,
            "evidence": self.evidence,
            "blocking": self.is_blocking,
        }


@dataclass
class RiskAssessmentResult:
    level: RiskLevel
    #: 0..100. Higher is riskier.
    score: Decimal
    signals: list[RiskSignal]
    summary: str
    model_version: str = RISK_MODEL_VERSION

    @property
    def blocking_signals(self) -> list[RiskSignal]:
        return [signal for signal in self.signals if signal.is_blocking]

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking_signals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "score": str(self.score),
            "summary": self.summary,
            "model_version": self.model_version,
            "signals": [signal.as_dict() for signal in self.signals],
        }


def _price_volatility_signal(context: AnalysisContext) -> RiskSignal | None:
    reference = context.target_prices.reference
    if reference is None or reference.volatility is None:
        return None
    volatility = reference.volatility
    if volatility >= Decimal("0.25"):
        severity = Severity.HIGH
    elif volatility >= Decimal("0.15"):
        severity = Severity.MEDIUM
    elif volatility >= Decimal("0.08"):
        severity = Severity.LOW
    else:
        return None
    return RiskSignal(
        code="price_volatility",
        severity=severity,
        weight=Decimal("1.0"),
        message=(
            f"Exit price is volatile: {volatility:.0%} coefficient of variation over "
            f"{reference.window_days} days."
        ),
        evidence=[
            f"Range {display(reference.minimum)} to {display(reference.maximum)} across "
            f"{reference.observation_count} observations.",
            f"Maximum drawdown {reference.max_drawdown:.0%}."
            if reference.max_drawdown is not None
            else "Drawdown not computable.",
        ],
    )


def _anomaly_signals(context: AnalysisContext) -> list[RiskSignal]:
    signals: list[RiskSignal] = []

    source = context.source_anomaly
    if source.anomaly_type is AnomalyType.POTENTIAL_CLEARANCE:
        signals.append(
            RiskSignal(
                code="source_clearance",
                severity=Severity.HIGH,
                weight=Decimal("1.0"),
                message=(
                    "The source price looks like clearance, not a repeatable cost: "
                    "it will not be there for a restock."
                ),
                evidence=source.evidence,
            )
        )
    elif source.anomaly_type in {AnomalyType.SEVERE_DISCOUNT, AnomalyType.PRICE_COLLAPSE}:
        signals.append(
            RiskSignal(
                code="source_price_anomaly",
                severity=Severity.MEDIUM,
                weight=Decimal("1.0"),
                message=(
                    f"{source.message} A price this far below its own history usually has "
                    "a reason that is not visible in the data."
                ),
                evidence=source.evidence,
            )
        )

    target = context.target_anomaly
    if target.anomaly_type in {
        AnomalyType.SEVERE_PREMIUM,
        AnomalyType.PRICE_SPIKE,
        AnomalyType.POTENTIAL_SHORTAGE,
    }:
        signals.append(
            RiskSignal(
                code="target_price_elevated",
                severity=Severity.HIGH,
                weight=Decimal("1.0"),
                message=(
                    f"{target.message} The profit projection rests on an exit price that is "
                    "above the level this listing normally holds."
                ),
                evidence=target.evidence,
            )
        )
    elif target.anomaly_type is AnomalyType.PRICE_COLLAPSE:
        signals.append(
            RiskSignal(
                code="target_price_collapse",
                severity=Severity.HIGH,
                weight=Decimal("1.0"),
                message=f"Exit price collapsed recently: {target.message}",
                evidence=target.evidence,
            )
        )
    elif target.anomaly_type is AnomalyType.UNKNOWN:
        signals.append(
            RiskSignal(
                code="no_price_baseline",
                severity=Severity.MEDIUM,
                weight=Decimal("1.0"),
                message=(
                    "There is no usable price history for the exit market, so the exit "
                    "price cannot be checked against a baseline."
                ),
                evidence=target.evidence,
            )
        )
    return signals


def _inventory_signal(context: AnalysisContext) -> RiskSignal | None:
    quantity = context.source.quantity_available
    availability = context.source.availability
    if availability is Availability.OUT_OF_STOCK:
        return RiskSignal(
            code="source_out_of_stock",
            severity=Severity.CRITICAL,
            weight=Decimal("1.0"),
            message="The source listing is out of stock: there is nothing to buy.",
            is_blocking=True,
        )
    if quantity is not None and quantity <= 3:
        return RiskSignal(
            code="very_low_source_inventory",
            severity=Severity.HIGH,
            weight=Decimal("1.0"),
            message=f"Only {quantity} unit(s) available at the source price.",
            evidence=[
                "A position this small cannot absorb meaningful capital, and the "
                "per-order handling cost is spread across very few units."
            ],
        )
    if quantity is not None and quantity <= 10:
        return RiskSignal(
            code="low_source_inventory",
            severity=Severity.MEDIUM,
            weight=Decimal("1.0"),
            message=f"Only {quantity} unit(s) available at the source price.",
            evidence=["Position size is capped by supply rather than by capital."],
        )
    if availability is Availability.LIMITED:
        return RiskSignal(
            code="limited_source_inventory",
            severity=Severity.LOW,
            weight=Decimal("1.0"),
            message="The source listing reports limited availability.",
        )
    return None


def _demand_signal(context: AnalysisContext) -> RiskSignal | None:
    demand = context.demand
    if demand.confidence is Confidence.NONE:
        return RiskSignal(
            code="no_demand_data",
            severity=Severity.MEDIUM,
            weight=Decimal("1.0"),
            message=(
                "No demand data is available. Whether this sells at all is unknown, "
                "which is not the same as it selling slowly."
            ),
            evidence=demand.reasons,
        )
    if demand.score is not None and demand.score < 25:
        return RiskSignal(
            code="weak_demand",
            severity=Severity.HIGH,
            weight=Decimal("1.0"),
            message=(
                f"Demand is weak: relative score {display_score(demand.score)} "
                f"from a median rank of {demand.median_rank:,}."
            ),
            evidence=demand.reasons,
        )
    if demand.rank_trend is TrendDirection.FALLING:
        return RiskSignal(
            code="demand_declining",
            severity=Severity.MEDIUM,
            weight=Decimal("1.0"),
            message="Sales rank has been deteriorating over the observed window.",
            evidence=demand.reasons,
        )
    if demand.confidence is Confidence.LOW:
        return RiskSignal(
            code="thin_demand_history",
            severity=Severity.LOW,
            weight=Decimal("1.0"),
            message=f"Demand history is thin: {demand.observation_count} observation(s).",
            evidence=demand.reasons,
        )
    return None


def _competition_signal(context: AnalysisContext) -> RiskSignal | None:
    competition = context.competition
    if competition.risk_level is RiskLevel.CRITICAL:
        severity = Severity.CRITICAL
    elif competition.risk_level is RiskLevel.HIGH:
        severity = Severity.HIGH
    elif competition.risk_level is RiskLevel.MEDIUM:
        severity = Severity.LOW
    else:
        return None
    return RiskSignal(
        code="competition",
        severity=severity,
        weight=Decimal("1.0"),
        message=f"Competition pressure is {competition.risk_level.value} "
        f"(score {display_score(competition.pressure_score)}).",
        evidence=competition.reasons,
    )


def _match_signal(context: AnalysisContext) -> RiskSignal | None:
    match = context.match
    if match.status is MatchStatus.REJECTED:
        return RiskSignal(
            code="match_rejected",
            severity=Severity.CRITICAL,
            weight=Decimal("1.0"),
            message=f"These listings are not the same product: {match.summary}",
            evidence=[conflict["message"] for conflict in match.conflicts] or [match.summary],
            is_blocking=True,
        )
    if match.status is MatchStatus.AMBIGUOUS:
        return RiskSignal(
            code="match_ambiguous",
            severity=Severity.HIGH,
            weight=Decimal("1.0"),
            message=(
                f"Product identity is ambiguous ({match.confidence:.0%} via "
                f"{match.method.value}). Buying against the wrong listing loses the "
                "whole position, not the margin."
            ),
            evidence=[item["detail"] for item in match.evidence],
            is_blocking=True,
        )
    if match.conflicts:
        return RiskSignal(
            code="variation_conflict",
            severity=Severity.MEDIUM,
            weight=Decimal("1.0"),
            message="The listings disagree on at least one variation attribute.",
            evidence=[conflict["message"] for conflict in match.conflicts],
        )
    if match.confidence < Decimal("0.85"):
        return RiskSignal(
            code="moderate_match_confidence",
            severity=Severity.LOW,
            weight=Decimal("1.0"),
            message=f"Match confidence is {match.confidence:.0%} via {match.method.value}.",
            evidence=[item["detail"] for item in match.evidence],
        )
    return None


def _quality_signal(context: AnalysisContext) -> RiskSignal | None:
    quality = context.quality
    if quality.score < 40:
        severity = Severity.HIGH
    elif quality.score < 65:
        severity = Severity.MEDIUM
    else:
        return None
    missing = ", ".join(item.value for item in quality.missing_dimensions) or "none"
    return RiskSignal(
        code="data_quality",
        severity=severity,
        weight=Decimal("1.0"),
        message=f"Data quality is {display_score(quality.score)}/100.",
        evidence=[
            f"Missing dimensions: {missing}.",
            *[
                f"{item.dimension.value}: {item.reason}"
                for item in quality.dimensions
                if item.confidence in {Confidence.NONE, Confidence.LOW}
            ],
        ],
    )


def _category_signal(context: AnalysisContext) -> RiskSignal | None:
    category = (context.category or "").lower()
    brand = (context.brand or "").lower()
    reasons: list[str] = []
    if category in ELEVATED_RISK_CATEGORIES:
        reasons.append(f"'{context.category}' is commonly gated or has above-average return rates.")
    if brand in ELEVATED_RISK_BRANDS:
        reasons.append(
            f"'{context.brand}' frequently restricts third-party sellers or enforces against them."
        )
    if not reasons:
        return None
    return RiskSignal(
        code="brand_category_risk",
        severity=Severity.MEDIUM if len(reasons) == 1 else Severity.HIGH,
        weight=Decimal("1.0"),
        message=(
            "Brand or category carries selling restrictions worth checking "
            "before committing capital."
        ),
        evidence=reasons,
    )


def _margin_signal(context: AnalysisContext) -> RiskSignal | None:
    result = context.profitability
    if result.net_profit <= 0:
        return RiskSignal(
            code="negative_profit",
            severity=Severity.CRITICAL,
            weight=Decimal("1.0"),
            message=f"Net profit is {display(result.net_profit)} at the observed prices.",
            evidence=[
                f"Total cost {display(result.total_cost)} against a sale price of "
                f"{display(result.sale_price)}."
            ],
            is_blocking=True,
        )
    if result.margin is not None and result.margin < Decimal("0.08"):
        return RiskSignal(
            code="thin_margin",
            severity=Severity.MEDIUM,
            weight=Decimal("1.0"),
            message=(
                f"Margin is {result.margin:.1%}. A fee change or a small price move erases it."
            ),
            evidence=[
                f"Break-even sale price {display(result.breakeven_sale_price)}, "
                f"currently {display(result.sale_price)}."
            ],
        )
    return None


def assess_risk(context: AnalysisContext) -> RiskAssessmentResult:
    """Collect every risk signal and reduce them to a level."""
    candidates = [
        _match_signal(context),
        _margin_signal(context),
        _price_volatility_signal(context),
        _inventory_signal(context),
        _demand_signal(context),
        _competition_signal(context),
        _quality_signal(context),
        _category_signal(context),
        *_anomaly_signals(context),
    ]
    signals = [signal for signal in candidates if signal is not None]

    if not signals:
        return RiskAssessmentResult(
            level=RiskLevel.LOW,
            score=Decimal("0"),
            signals=[],
            summary="No risk signals were raised by the data available.",
        )

    # Highest signal dominates, with the remainder adding a decaying tail. A sum
    # would let five low signals outweigh one critical one, which inverts the
    # ordering that actually matters when deciding to spend money.
    ordered = sorted(signals, key=lambda signal: signal.points, reverse=True)
    score = ordered[0].points
    for index, signal in enumerate(ordered[1:], start=1):
        score += signal.points / Decimal(2 * index)
    score = ratio(min(Decimal("100"), score))

    blocking = [signal for signal in signals if signal.is_blocking]
    if blocking:
        level = RiskLevel.CRITICAL
    elif score >= 55:
        level = RiskLevel.HIGH
    elif score >= 28:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    headline = ordered[0]
    summary = (
        f"{level.value.title()} risk ({display_score(score)}/100). "
        f"Leading signal: {headline.message}"
        if not blocking
        else f"Critical risk. {blocking[0].message}"
    )
    return RiskAssessmentResult(level=level, score=score, signals=ordered, summary=summary)
