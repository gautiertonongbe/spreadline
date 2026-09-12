"""Opportunity scoring.

A configurable, fully transparent weighted model (spec §17). The total is never
shown without its components, every component states the basis it was computed
from, and the model version travels with every stored score so a weight change
does not silently invalidate historical comparisons.

Scores are deliberately not probabilities. They rank candidates against each
other under one explicit policy; they do not claim to be a likelihood of anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import display, display_score, ratio, to_decimal
from app.domains.opportunities.context import AnalysisContext
from app.domains.risk.engine import RiskAssessmentResult
from app.models.enums import Availability, Confidence, MatchStatus  # noqa: F401


@dataclass(frozen=True)
class ScoringModel:
    """Weights and curves. Everything here is meant to be tuned by the operator."""

    version: str = "score-v1"
    weight_profit: Decimal = Decimal("0.25")
    weight_roi: Decimal = Decimal("0.20")
    weight_price_stability: Decimal = Decimal("0.15")
    weight_demand: Decimal = Decimal("0.15")
    weight_competition: Decimal = Decimal("0.10")
    weight_match_confidence: Decimal = Decimal("0.05")
    weight_availability: Decimal = Decimal("0.05")
    weight_risk: Decimal = Decimal("0.05")

    #: (net profit per unit, score) breakpoints, linearly interpolated.
    profit_curve: tuple[tuple[Decimal, Decimal], ...] = (
        (Decimal("0"), Decimal("0")),
        (Decimal("3"), Decimal("30")),
        (Decimal("6"), Decimal("50")),
        (Decimal("10"), Decimal("70")),
        (Decimal("20"), Decimal("88")),
        (Decimal("40"), Decimal("100")),
    )
    roi_curve: tuple[tuple[Decimal, Decimal], ...] = (
        (Decimal("0"), Decimal("0")),
        (Decimal("0.10"), Decimal("25")),
        (Decimal("0.20"), Decimal("50")),
        (Decimal("0.30"), Decimal("68")),
        (Decimal("0.50"), Decimal("86")),
        (Decimal("1.00"), Decimal("100")),
    )
    #: Score assigned to a component that has no data behind it. Below the
    #: mid-point on purpose: absence of evidence is not neutral when capital is
    #: about to move.
    unknown_component_score: Decimal = Decimal("30")

    @property
    def weights(self) -> dict[str, Decimal]:
        return {
            "profit": self.weight_profit,
            "roi": self.weight_roi,
            "price_stability": self.weight_price_stability,
            "demand": self.weight_demand,
            "competition": self.weight_competition,
            "match_confidence": self.weight_match_confidence,
            "availability": self.weight_availability,
            "risk": self.weight_risk,
        }

    def validate(self) -> None:
        total = sum(self.weights.values())
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise ValueError(f"Scoring weights must sum to 1.0; they sum to {total}.")


DEFAULT_SCORING_MODEL = ScoringModel()


@dataclass
class ScoreComponent:
    name: str
    score: Decimal
    weight: Decimal
    basis: str
    #: False when the component fell back to ``unknown_component_score``.
    has_data: bool = True

    @property
    def contribution(self) -> Decimal:
        return ratio(self.score * self.weight)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": str(self.score),
            "weight": str(self.weight),
            "contribution": str(self.contribution),
            "basis": self.basis,
            "has_data": self.has_data,
        }


@dataclass
class OpportunityScore:
    total: Decimal
    components: list[ScoreComponent]
    model_version: str
    #: Components that had no data. A high total built on three of these means
    #: something different from a high total built on eight measured ones.
    unknown_components: list[str] = field(default_factory=list)
    #: Set when the candidate cannot be bought at all, in which case ``total`` is
    #: zero and ``raw_total`` holds what the weighted model produced.
    gated_reason: str | None = None
    raw_total: Decimal | None = None

    def component(self, name: str) -> ScoreComponent | None:
        return next((item for item in self.components if item.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": str(self.total),
            "raw_total": None if self.raw_total is None else str(self.raw_total),
            "gated_reason": self.gated_reason,
            "model_version": self.model_version,
            "components": [item.as_dict() for item in self.components],
            "unknown_components": self.unknown_components,
        }


def _interpolate(curve: Sequence[tuple[Decimal, Decimal]], value: Decimal) -> Decimal:
    """Piecewise-linear lookup, clamped at both ends."""
    if value <= curve[0][0]:
        return curve[0][1]
    for (low_x, low_y), (high_x, high_y) in zip(curve, curve[1:], strict=False):
        if value <= high_x:
            span = high_x - low_x
            if span == 0:
                return high_y
            weight = (value - low_x) / span
            return ratio(low_y + (high_y - low_y) * weight)
    return curve[-1][1]


def _profit_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    profit = context.profitability.net_profit
    return ScoreComponent(
        name="profit",
        score=_interpolate(model.profit_curve, profit),
        weight=model.weight_profit,
        basis=f"Net profit of {display(profit)} per unit.",
    )


def _roi_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    roi = context.profitability.roi
    if roi is None:
        return ScoreComponent(
            name="roi",
            score=model.unknown_component_score,
            weight=model.weight_roi,
            basis="ROI is undefined because invested capital is zero.",
            has_data=False,
        )
    return ScoreComponent(
        name="roi",
        score=_interpolate(model.roi_curve, roi),
        weight=model.weight_roi,
        basis=f"ROI of {roi:.1%} on invested capital.",
    )


def _price_stability_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    reference = context.target_prices.reference
    if reference is None or reference.volatility is None:
        return ScoreComponent(
            name="price_stability",
            score=model.unknown_component_score,
            weight=model.weight_price_stability,
            basis=(
                f"No usable price history for the exit market "
                f"({context.target_prices.observation_count} observation(s))."
            ),
            has_data=False,
        )
    volatility = reference.volatility
    # 0% variation scores 100, 30% or more scores 0.
    raw = (Decimal("1") - min(Decimal("1"), volatility / Decimal("0.30"))) * Decimal("100")
    drawdown_penalty = Decimal("0")
    if reference.max_drawdown is not None and reference.max_drawdown > Decimal("0.25"):
        drawdown_penalty = Decimal("15")
    score = max(Decimal("0"), ratio(raw - drawdown_penalty))
    return ScoreComponent(
        name="price_stability",
        score=score,
        weight=model.weight_price_stability,
        basis=(
            f"{volatility:.0%} coefficient of variation over {reference.window_days} days"
            + (
                f", maximum drawdown {reference.max_drawdown:.0%}."
                if reference.max_drawdown is not None
                else "."
            )
        ),
    )


def _demand_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    demand = context.demand
    if demand.score is None or demand.confidence is Confidence.NONE:
        return ScoreComponent(
            name="demand",
            score=model.unknown_component_score,
            weight=model.weight_demand,
            basis="No demand data available; scored as unknown rather than as zero demand.",
            has_data=False,
        )
    score = demand.score
    if demand.confidence is Confidence.LOW:
        # Discount a score that rests on very little evidence rather than
        # pretending the evidence is stronger than it is.
        score = ratio(score * Decimal("0.75"))
    return ScoreComponent(
        name="demand",
        score=score,
        weight=model.weight_demand,
        basis=(
            f"Relative demand {display_score(demand.score)} from a median rank of "
            f"{demand.median_rank:,}"
            f" ({demand.confidence.value} confidence)."
        ),
    )


def _competition_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    competition = context.competition
    if competition.confidence is Confidence.NONE:
        return ScoreComponent(
            name="competition",
            score=model.unknown_component_score,
            weight=model.weight_competition,
            basis="No competition data available.",
            has_data=False,
        )
    return ScoreComponent(
        name="competition",
        score=ratio(Decimal("100") - competition.pressure_score),
        weight=model.weight_competition,
        basis=(
            f"{competition.seller_count} seller(s), pressure score "
            f"{display_score(competition.pressure_score)}/100."
        ),
    )


def _match_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    match = context.match
    return ScoreComponent(
        name="match_confidence",
        score=ratio(match.confidence * Decimal("100")),
        weight=model.weight_match_confidence,
        basis=f"{match.confidence:.0%} via {match.method.value} ({match.status.value}).",
    )


def _availability_component(context: AnalysisContext, model: ScoringModel) -> ScoreComponent:
    availability = context.source.availability
    quantity = context.source.quantity_available
    if availability is Availability.OUT_OF_STOCK:
        score, basis = Decimal("0"), "Source is out of stock."
    elif availability is Availability.UNKNOWN and quantity is None:
        return ScoreComponent(
            name="availability",
            score=model.unknown_component_score,
            weight=model.weight_availability,
            basis="Source availability is unknown.",
            has_data=False,
        )
    elif quantity is not None and quantity <= 3:
        score, basis = Decimal("25"), f"Only {quantity} unit(s) available."
    elif quantity is not None and quantity <= 10:
        score, basis = Decimal("55"), f"{quantity} units available."
    elif availability is Availability.LIMITED:
        score, basis = Decimal("45"), "Source reports limited availability."
    else:
        score = Decimal("100")
        basis = (
            f"In stock with {quantity} units available." if quantity is not None else "In stock."
        )
    return ScoreComponent(
        name="availability", score=score, weight=model.weight_availability, basis=basis
    )


def _risk_component(risk: RiskAssessmentResult, model: ScoringModel) -> ScoreComponent:
    return ScoreComponent(
        name="risk",
        score=ratio(Decimal("100") - risk.score),
        weight=model.weight_risk,
        basis=f"Risk level {risk.level.value} (score {display_score(risk.score)}/100).",
    )


def score_opportunity(
    context: AnalysisContext,
    risk: RiskAssessmentResult,
    *,
    model: ScoringModel = DEFAULT_SCORING_MODEL,
) -> OpportunityScore:
    model.validate()
    components = [
        _profit_component(context, model),
        _roi_component(context, model),
        _price_stability_component(context, model),
        _demand_component(context, model),
        _competition_component(context, model),
        _match_component(context, model),
        _availability_component(context, model),
        _risk_component(risk, model),
    ]
    total = ratio(sum((component.contribution for component in components), Decimal("0")))
    total = min(Decimal("100"), max(Decimal("0"), total))

    # A candidate that cannot be bought must not rank against ones that can.
    # Without this, a rejected product match with a large apparent spread sorts to
    # the top of the opportunity table and reads as the best idea on the screen.
    gated_reason = None
    if not context.match.is_usable:
        gated_reason = f"Product identity is not established: {context.match.summary}"
    elif context.profitability.net_profit <= 0:
        gated_reason = f"Net profit is {context.profitability.net_profit} at the observed prices."
    elif context.source.availability is Availability.OUT_OF_STOCK:
        gated_reason = "The source listing is out of stock."
    elif risk.has_blocking:
        gated_reason = risk.blocking_signals[0].message

    return OpportunityScore(
        total=Decimal("0") if gated_reason else total,
        raw_total=total if gated_reason else None,
        gated_reason=gated_reason,
        components=components,
        model_version=model.version,
        unknown_components=[item.name for item in components if not item.has_data],
    )


def model_from_overrides(overrides: dict[str, Any] | None) -> ScoringModel:
    """Build a scoring model from stored org/user preferences."""
    if not overrides:
        return DEFAULT_SCORING_MODEL
    fields = {
        key: to_decimal(value)
        for key, value in overrides.items()
        if key.startswith("weight_") and key in ScoringModel.__dataclass_fields__
    }
    if "version" in overrides:
        fields["version"] = overrides["version"]  # type: ignore[assignment]
    if not fields:
        return DEFAULT_SCORING_MODEL
    model = ScoringModel(**{**DEFAULT_SCORING_MODEL.__dict__, **fields})
    model.validate()
    return model
