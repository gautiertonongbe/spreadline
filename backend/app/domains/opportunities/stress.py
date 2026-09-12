"""Scenario and stress testing.

Any spread looks good at the prices showing today. The question that decides
whether to commit capital is what happens when they move (spec §19).

Each scenario is an explicit, named set of shocks to the inputs, re-run through
the same deterministic profitability engine. Nothing is approximated by scaling
the base result: the fees are recomputed, because a sale price 20% lower changes
the referral fee and the return allowance too.

Where a scenario models a second-order effect, the assumption is stated in the
scenario itself rather than buried in a coefficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import money, ratio
from app.domains.opportunities.context import AnalysisContext
from app.domains.profitability.assumptions import FeeAssumptions
from app.domains.profitability.engine import (
    ProfitabilityInput,
    ProfitabilityResult,
    calculate_profitability,
)
from app.models.enums import RiskLevel

#: Price erosion caused by one additional seller entering, before diminishing
#: returns. An assumption, stated here so it can be argued with and tuned.
PRICE_EROSION_PER_SELLER = Decimal("0.012")
MAX_COMPETITION_EROSION = Decimal("0.25")


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    description: str
    sale_price_multiplier: Decimal = Decimal("1")
    acquisition_multiplier: Decimal = Decimal("1")
    #: Extra sellers entering the listing. Converted to price erosion.
    additional_sellers: int = 0
    #: Change in velocity, as a multiplier. 0.7 means demand falls 30%.
    velocity_multiplier: Decimal = Decimal("1")


#: The standard battery (spec §19).
DEFAULT_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="base",
        label="Base case",
        description="Current observed prices and assumptions.",
    ),
    Scenario(
        key="downside",
        label="Downside",
        description="Selling price falls 10%.",
        sale_price_multiplier=Decimal("0.90"),
    ),
    Scenario(
        key="severe_downside",
        label="Severe downside",
        description="Selling price falls 20%.",
        sale_price_multiplier=Decimal("0.80"),
    ),
    Scenario(
        key="acquisition_shock",
        label="Acquisition shock",
        description="Acquisition price rises 10%: the source price was not repeatable.",
        acquisition_multiplier=Decimal("1.10"),
    ),
    Scenario(
        key="competition_shock",
        label="Competition shock",
        description=("Ten additional sellers enter, eroding the achievable selling price."),
        additional_sellers=10,
    ),
    Scenario(
        key="demand_shock",
        label="Demand shock",
        description=("Velocity falls 30%: the unit sits in storage longer before it sells."),
        velocity_multiplier=Decimal("0.70"),
    ),
    Scenario(
        key="combined_downside",
        label="Combined downside",
        description=(
            "Selling price falls 15%, acquisition rises 5%, five sellers enter and "
            "velocity falls 30%. Adverse conditions arrive together, not one at a time."
        ),
        sale_price_multiplier=Decimal("0.85"),
        acquisition_multiplier=Decimal("1.05"),
        additional_sellers=5,
        velocity_multiplier=Decimal("0.70"),
    ),
)


@dataclass
class ScenarioResult:
    scenario: Scenario
    sale_price: Decimal
    acquisition_cost: Decimal
    total_fees: Decimal
    net_profit: Decimal
    roi: Decimal | None
    margin: Decimal | None
    months_in_storage: Decimal
    risk_level: RiskLevel
    #: Change in profit against the base case.
    profit_delta: Decimal | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def survives(self) -> bool:
        return self.net_profit > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.scenario.key,
            "label": self.scenario.label,
            "description": self.scenario.description,
            "sale_price": str(self.sale_price),
            "acquisition_cost": str(self.acquisition_cost),
            "total_fees": str(self.total_fees),
            "net_profit": str(self.net_profit),
            "roi": None if self.roi is None else str(self.roi),
            "margin": None if self.margin is None else str(self.margin),
            "months_in_storage": str(self.months_in_storage),
            "risk_level": self.risk_level.value,
            "profit_delta": None if self.profit_delta is None else str(self.profit_delta),
            "survives": self.survives,
            "notes": self.notes,
        }


@dataclass
class StressTestResult:
    scenarios: list[ScenarioResult]
    #: How many of the non-base scenarios still turn a profit.
    surviving_count: int
    total_count: int
    worst_case: ScenarioResult | None
    #: The share of the sale price that can be lost before profit hits zero.
    price_headroom: Decimal | None

    @property
    def survival_rate(self) -> Decimal:
        if not self.total_count:
            return Decimal("0")
        return ratio(Decimal(self.surviving_count) / Decimal(self.total_count))

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [item.as_dict() for item in self.scenarios],
            "surviving_count": self.surviving_count,
            "total_count": self.total_count,
            "survival_rate": str(self.survival_rate),
            "worst_case": self.worst_case.as_dict() if self.worst_case else None,
            "price_headroom": None if self.price_headroom is None else str(self.price_headroom),
        }


def _risk_for(result: ProfitabilityResult) -> RiskLevel:
    if result.net_profit <= 0:
        return RiskLevel.CRITICAL
    if result.roi is None or result.roi < Decimal("0.10"):
        return RiskLevel.HIGH
    if result.roi < Decimal("0.20"):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _competition_erosion(additional_sellers: int, current_sellers: int | None) -> Decimal:
    """Price erosion from new entrants, with diminishing marginal impact.

    Entrants hurt most on a listing that has few sellers today. Going from 2 to 12
    changes who wins the buy box; going from 30 to 40 changes very little that is
    not already priced in.
    """
    if additional_sellers <= 0:
        return Decimal("0")
    base = Decimal(additional_sellers) * PRICE_EROSION_PER_SELLER
    if current_sellers and current_sellers > 5:
        base *= Decimal("5") / Decimal(current_sellers)
    return min(MAX_COMPETITION_EROSION, ratio(base))


def run_scenario(
    context: AnalysisContext,
    scenario: Scenario,
    *,
    assumptions: FeeAssumptions | None = None,
    weight_lb: Decimal | None = None,
    cubic_feet: Decimal | None = None,
) -> ScenarioResult:
    base = context.profitability
    fees = assumptions or base.assumptions
    notes: list[str] = []

    sale_price = money(base.sale_price * scenario.sale_price_multiplier)
    erosion = _competition_erosion(scenario.additional_sellers, context.competition.seller_count)
    if erosion:
        sale_price = money(sale_price * (Decimal("1") - erosion))
        notes.append(
            f"{scenario.additional_sellers} additional sellers modelled as {erosion:.1%} "
            f"price erosion (assumption: {PRICE_EROSION_PER_SELLER:.1%} per seller, "
            "with diminishing impact on already-crowded listings)."
        )

    acquisition = money(base.acquisition_cost * scenario.acquisition_multiplier)

    months = fees.expected_months_in_storage
    if scenario.velocity_multiplier != 1 and scenario.velocity_multiplier > 0:
        months = ratio(months / scenario.velocity_multiplier)
        notes.append(
            f"Velocity at {scenario.velocity_multiplier:.0%} extends expected storage from "
            f"{fees.expected_months_in_storage} to {months} months."
        )

    result = calculate_profitability(
        ProfitabilityInput(
            sale_price=sale_price,
            acquisition_cost=acquisition,
            target_marketplace=context.target.marketplace,
            acquisition_shipping=base.acquisition_shipping,
            category=context.category,
            weight_lb=weight_lb,
            cubic_feet=cubic_feet,
            fulfillment=base.fulfillment,
            months_in_storage=months,
            misc_cost=base.misc_cost,
        ),
        fees,
    )

    return ScenarioResult(
        scenario=scenario,
        sale_price=result.sale_price,
        acquisition_cost=result.acquisition_cost,
        total_fees=result.total_fees,
        net_profit=result.net_profit,
        roi=result.roi,
        margin=result.margin,
        months_in_storage=months,
        risk_level=_risk_for(result),
        notes=notes,
    )


def run_stress_test(
    context: AnalysisContext,
    *,
    scenarios: tuple[Scenario, ...] = DEFAULT_SCENARIOS,
    assumptions: FeeAssumptions | None = None,
    weight_lb: Decimal | None = None,
    cubic_feet: Decimal | None = None,
) -> StressTestResult:
    results = [
        run_scenario(
            context, scenario, assumptions=assumptions, weight_lb=weight_lb, cubic_feet=cubic_feet
        )
        for scenario in scenarios
    ]
    base_profit = next((item.net_profit for item in results if item.scenario.key == "base"), None)
    if base_profit is not None:
        for item in results:
            item.profit_delta = money(item.net_profit - base_profit)

    adverse = [item for item in results if item.scenario.key != "base"]
    surviving = [item for item in adverse if item.survives]
    worst = min(adverse, key=lambda item: item.net_profit) if adverse else None

    headroom = None
    breakeven = context.profitability.breakeven_sale_price
    if breakeven is not None and context.profitability.sale_price > 0:
        headroom = ratio(
            (context.profitability.sale_price - breakeven) / context.profitability.sale_price
        )

    return StressTestResult(
        scenarios=results,
        surviving_count=len(surviving),
        total_count=len(adverse),
        worst_case=worst,
        price_headroom=headroom,
    )
