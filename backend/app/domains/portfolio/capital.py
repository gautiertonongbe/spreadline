"""Capital allocation.

Given available capital and a set of constraints, decide how much to put behind
each opportunity (spec §20, §29). This is where the platform stops being a scanner
and starts answering the question that actually matters: where is the best use of
inventory capital right now.

The allocator is a constrained greedy pass over risk-adjusted profit per dollar.
That is a deliberate choice, not a placeholder for a solver: the objective is
explainable, the ordering is stable, and every exclusion is reported with its
reason. Portfolio optimisation with covariance between positions is a later step
and needs outcome data this platform has not collected yet.

Two things it refuses to do:

* allocate more units than the source actually has available, and
* let a single product, brand or category quietly become the whole book.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import display, money, pct_of, ratio
from app.models.enums import RiskLevel

#: Multiplier applied to expected profit at each risk level when ranking. Not a
#: probability of failure: a preference ordering that says a medium-risk dollar
#: of profit is worth less than a low-risk one.
RISK_DISCOUNT: dict[RiskLevel, Decimal] = {
    RiskLevel.LOW: Decimal("1.00"),
    RiskLevel.MEDIUM: Decimal("0.80"),
    RiskLevel.HIGH: Decimal("0.55"),
    RiskLevel.CRITICAL: Decimal("0.00"),
}


@dataclass
class AllocationCandidate:
    """One opportunity, reduced to what allocation needs."""

    opportunity_id: str
    title: str
    unit_cost: Decimal
    unit_profit: Decimal
    roi: Decimal | None
    risk_level: RiskLevel
    score: Decimal
    #: None means supply is unknown, which is treated as a constraint, not as
    #: infinite supply.
    units_available: int | None = None
    brand: str | None = None
    category: str | None = None
    recommendation: str = "review"

    @property
    def risk_adjusted_profit(self) -> Decimal:
        return money(self.unit_profit * RISK_DISCOUNT[self.risk_level])

    @property
    def profit_per_dollar(self) -> Decimal:
        if self.unit_cost <= 0:
            return Decimal("0")
        return ratio(self.risk_adjusted_profit / self.unit_cost)


@dataclass(frozen=True)
class CapitalConstraints:
    available_capital: Decimal
    #: Cap per position, absolute and as a share of capital. The tighter binds.
    max_position_size: Decimal | None = None
    max_position_pct: Decimal = Decimal("0.25")
    min_roi: Decimal = Decimal("0.20")
    max_risk_level: RiskLevel = RiskLevel.MEDIUM
    min_score: Decimal = Decimal("50")
    max_positions: int | None = None
    #: Diversification: share of deployed capital allowed in one brand/category.
    max_brand_pct: Decimal = Decimal("0.40")
    max_category_pct: Decimal = Decimal("0.50")
    #: Positions below this are not worth the handling time.
    min_position_size: Decimal = Decimal("50")
    #: When supply is unknown, cap the position at this many units rather than
    #: assuming the source can fill an arbitrary order.
    default_units_when_unknown: int = 10
    #: Only allocate to recommendations in this set.
    allowed_recommendations: tuple[str, ...] = ("buy",)

    def position_cap(self) -> Decimal:
        by_pct = money(self.available_capital * self.max_position_pct)
        if self.max_position_size is None:
            return by_pct
        return min(by_pct, money(self.max_position_size))


@dataclass
class Allocation:
    candidate: AllocationCandidate
    units: int
    capital: Decimal
    expected_profit: Decimal
    expected_roi: Decimal | None
    #: Which constraint stopped this position from being larger.
    limited_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.candidate.opportunity_id,
            "title": self.candidate.title,
            "units": self.units,
            "unit_cost": str(self.candidate.unit_cost),
            "capital": str(self.capital),
            "expected_profit": str(self.expected_profit),
            "expected_roi": None if self.expected_roi is None else str(self.expected_roi),
            "risk_level": self.candidate.risk_level.value,
            "score": str(self.candidate.score),
            "brand": self.candidate.brand,
            "category": self.candidate.category,
            "limited_by": self.limited_by,
        }


@dataclass
class Exclusion:
    opportunity_id: str
    title: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass
class CapitalPlanResult:
    allocations: list[Allocation]
    excluded: list[Exclusion]
    allocated_capital: Decimal
    unallocated_capital: Decimal
    expected_profit: Decimal
    expected_roi: Decimal | None
    constraints: CapitalConstraints
    model_version: str = "greedy-v1"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocations": [item.as_dict() for item in self.allocations],
            "excluded": [item.as_dict() for item in self.excluded],
            "allocated_capital": str(self.allocated_capital),
            "unallocated_capital": str(self.unallocated_capital),
            "expected_profit": str(self.expected_profit),
            "expected_roi": None if self.expected_roi is None else str(self.expected_roi),
            "position_count": len(self.allocations),
            "model_version": self.model_version,
            "notes": self.notes,
            "constraints": {
                "available_capital": str(self.constraints.available_capital),
                "max_position_size": (
                    None
                    if self.constraints.max_position_size is None
                    else str(self.constraints.max_position_size)
                ),
                "max_position_pct": str(self.constraints.max_position_pct),
                "min_roi": str(self.constraints.min_roi),
                "max_risk_level": self.constraints.max_risk_level.value,
                "min_score": str(self.constraints.min_score),
                "max_positions": self.constraints.max_positions,
                "max_brand_pct": str(self.constraints.max_brand_pct),
                "max_category_pct": str(self.constraints.max_category_pct),
                "allowed_recommendations": list(self.constraints.allowed_recommendations),
            },
        }


def _eligibility_failure(
    candidate: AllocationCandidate, constraints: CapitalConstraints
) -> str | None:
    if candidate.recommendation not in constraints.allowed_recommendations:
        return (
            f"Recommendation is {candidate.recommendation.upper()}; this plan allocates only to "
            + ", ".join(item.upper() for item in constraints.allowed_recommendations)
            + "."
        )
    if candidate.unit_profit <= 0:
        return f"Unit profit is {display(candidate.unit_profit)}."
    if candidate.unit_cost <= 0:
        return "Unit cost is zero or unknown, so a position cannot be sized."
    if candidate.risk_level.rank > constraints.max_risk_level.rank:
        return (
            f"Risk level {candidate.risk_level.value} exceeds the "
            f"{constraints.max_risk_level.value} limit."
        )
    if candidate.roi is None or candidate.roi < constraints.min_roi:
        shown = "undefined" if candidate.roi is None else f"{candidate.roi:.1%}"
        return f"ROI {shown} is below the {constraints.min_roi:.0%} minimum."
    if candidate.score < constraints.min_score:
        return f"Score {candidate.score} is below the {constraints.min_score} minimum."
    if candidate.units_available is not None and candidate.units_available < 1:
        return "No units are available at the source price."
    return None


def allocate_capital(
    candidates: Sequence[AllocationCandidate], constraints: CapitalConstraints
) -> CapitalPlanResult:
    allocations: list[Allocation] = []
    excluded: list[Exclusion] = []
    notes: list[str] = []

    eligible: list[AllocationCandidate] = []
    for candidate in candidates:
        failure = _eligibility_failure(candidate, constraints)
        if failure:
            excluded.append(Exclusion(candidate.opportunity_id, candidate.title, failure))
        else:
            eligible.append(candidate)

    # Rank by risk-adjusted profit per dollar; score breaks ties so the ordering
    # is deterministic and reproducible across runs.
    eligible.sort(
        key=lambda item: (item.profit_per_dollar, item.score, item.opportunity_id),
        reverse=True,
    )

    remaining = money(constraints.available_capital)
    position_cap = constraints.position_cap()
    brand_capital: dict[str, Decimal] = {}
    category_capital: dict[str, Decimal] = {}

    for candidate in eligible:
        if constraints.max_positions is not None and len(allocations) >= constraints.max_positions:
            excluded.append(
                Exclusion(
                    candidate.opportunity_id,
                    candidate.title,
                    f"Position limit of {constraints.max_positions} already reached.",
                )
            )
            continue
        if remaining < constraints.min_position_size:
            excluded.append(
                Exclusion(
                    candidate.opportunity_id,
                    candidate.title,
                    f"Only {display(remaining)} of capital remains, below the "
                    "minimum position size.",
                )
            )
            continue

        limits: list[tuple[Decimal, str]] = [
            (remaining, "remaining capital"),
            (position_cap, "maximum position size"),
        ]

        # Diversification limits are computed against the capital deployed so
        # far plus this position, so the first position cannot breach them alone
        # unless it exceeds the cap on its own.
        deployed = money(constraints.available_capital - remaining)
        brand = (candidate.brand or "").lower()
        if brand:
            brand_budget = money(
                (deployed + remaining) * constraints.max_brand_pct
            ) - brand_capital.get(brand, Decimal("0"))
            limits.append(
                (max(Decimal("0"), brand_budget), f"brand concentration cap ({candidate.brand})")
            )
        category = (candidate.category or "").lower()
        if category:
            category_budget = money(
                (deployed + remaining) * constraints.max_category_pct
            ) - category_capital.get(category, Decimal("0"))
            limits.append(
                (
                    max(Decimal("0"), category_budget),
                    f"category concentration cap ({candidate.category})",
                )
            )

        budget, limiting_reason = min(limits, key=lambda item: item[0])
        max_units_by_budget = int(budget // candidate.unit_cost)

        supply = (
            candidate.units_available
            if candidate.units_available is not None
            else constraints.default_units_when_unknown
        )
        if candidate.units_available is None:
            supply_reason = "unknown supply (capped at the default position size)"
        else:
            supply_reason = "units available at the source"

        units = min(max_units_by_budget, supply)
        if units < 1:
            excluded.append(
                Exclusion(
                    candidate.opportunity_id,
                    candidate.title,
                    f"A single unit at {display(candidate.unit_cost)} does not fit "
                    f"the {limiting_reason}.",
                )
            )
            continue

        capital = money(candidate.unit_cost * units)
        if capital < constraints.min_position_size:
            excluded.append(
                Exclusion(
                    candidate.opportunity_id,
                    candidate.title,
                    f"Position of {display(capital)} is below the "
                    f"{display(constraints.min_position_size)} minimum.",
                )
            )
            continue

        limited_by = supply_reason if units == supply else limiting_reason
        profit = money(candidate.unit_profit * units)
        allocations.append(
            Allocation(
                candidate=candidate,
                units=units,
                capital=capital,
                expected_profit=profit,
                expected_roi=pct_of(profit, capital),
                limited_by=limited_by,
            )
        )
        remaining = money(remaining - capital)
        if brand:
            brand_capital[brand] = brand_capital.get(brand, Decimal("0")) + capital
        if category:
            category_capital[category] = category_capital.get(category, Decimal("0")) + capital

    allocated = money(sum((item.capital for item in allocations), Decimal("0")))
    expected_profit = money(sum((item.expected_profit for item in allocations), Decimal("0")))

    if remaining > 0 and allocations:
        notes.append(
            f"{display(remaining)} of capital is unallocated: the remaining candidates were "
            "excluded by supply, concentration or eligibility limits rather than by capital."
        )
    if not allocations:
        notes.append("No candidate satisfied the constraints; no capital was allocated.")

    return CapitalPlanResult(
        allocations=allocations,
        excluded=excluded,
        allocated_capital=allocated,
        unallocated_capital=remaining,
        expected_profit=expected_profit,
        expected_roi=pct_of(expected_profit, allocated),
        constraints=constraints,
        notes=notes,
    )
